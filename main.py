import logging
import sys
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from database import engine, Base, SessionLocal
import models
from auth import router as auth_router, hash_password, make_token
from messages import router as msg_router
from ws import manager

# ── Логирование ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("nexus.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── БД ────────────────────────────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)
logger.info("Таблицы БД инициализированы")


def seed_users():
    """Создаёт тестовых пользователей при первом запуске."""
    db = SessionLocal()
    try:
        test_users = [
            {
                "username": "alex_dev",
                "email": "alex@nexus.local",
                "password": "test123",
                "about": "Разработчик. Люблю Python и чистый код.",
            },
            {
                "username": "maria_design",
                "email": "maria@nexus.local",
                "password": "test123",
                "about": "UI/UX дизайнер. Создаю красивые интерфейсы.",
            },
            {
                "username": "ivan_chat",
                "email": "ivan@nexus.local",
                "password": "test123",
                "about": "Просто общаюсь.",
            },
        ]
        for u in test_users:
            if not db.query(models.User).filter_by(email=u["email"]).first():
                user = models.User(
                    username=u["username"],
                    email=u["email"],
                    password=hash_password(u["password"]),
                    about=u.get("about"),
                )
                db.add(user)
        db.commit()

        # Достижение «Первый день» для всех
        for user in db.query(models.User).all():
            exists = db.query(models.Achievement).filter_by(
                user_id=user.id, code="first_day"
            ).first()
            if not exists:
                db.add(models.Achievement(
                    user_id=user.id,
                    code="first_day",
                    title="Первый день в Nexus",
                ))
        db.commit()
        logger.info("Тестовые пользователи готовы")
    finally:
        db.close()


seed_users()

# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(title="Nexus API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(msg_router)

# Статика (загруженные файлы, аватары и т.д.)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Страницы ──────────────────────────────────────────────────────────────────
@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.png", include_in_schema=False)
def favicon():
    return FileResponse("static/favicon.png", media_type="image/png")

@app.get("/")
@app.get("/auth")
def auth_page():
    """Стартовая страница — всегда экран входа/регистрации."""
    return FileResponse("static/auth.html")


@app.get("/app")
def app_page():
    """Главный экран — только после авторизации."""
    return FileResponse("static/app.html")


# ── WebSocket ─────────────────────────────────────────────────────────────────
def _user_by_token(token: str):
    """Возвращает User по токену или None."""
    db = SessionLocal()
    try:
        for user in db.query(models.User).all():
            if make_token(user.id, user.email) == token:
                return user
        return None
    finally:
        db.close()


def _chat_member_ids(chat_id: int) -> list[int]:
    db = SessionLocal()
    try:
        chat = db.query(models.Chat).filter_by(id=chat_id).first()
        if not chat:
            return []
        return [m.id for m in chat.members]
    finally:
        db.close()


def _set_status(user_id: int, status: str):
    db = SessionLocal()
    try:
        from datetime import datetime
        user = db.query(models.User).filter_by(id=user_id).first()
        if user:
            user.status = status
            user.last_seen = datetime.utcnow()
            db.commit()
    finally:
        db.close()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(...)):
    user = _user_by_token(token)
    if not user:
        await ws.close(code=4001)
        return

    user_id = user.id
    await manager.connect(ws, user_id)
    _set_status(user_id, "online")

    # Уведомить всех что пользователь онлайн
    await manager.broadcast_all({
        "type": "status",
        "user_id": user_id,
        "status": "online",
    })

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                continue

            msg_type = data.get("type")

            # Клиент сообщает что печатает
            if msg_type == "typing":
                chat_id = data.get("chat_id")
                if chat_id:
                    member_ids = _chat_member_ids(chat_id)
                    await manager.broadcast_chat(
                        member_ids,
                        {
                            "type": "typing",
                            "chat_id": chat_id,
                            "user_id": user_id,
                            "username": user.username,
                        },
                        exclude=user_id,
                    )

            # Клиент помечает сообщения прочитанными
            elif msg_type == "read":
                chat_id = data.get("chat_id")
                if chat_id:
                    member_ids = _chat_member_ids(chat_id)
                    await manager.broadcast_chat(
                        member_ids,
                        {
                            "type": "read",
                            "chat_id": chat_id,
                            "user_id": user_id,
                        },
                        exclude=user_id,
                    )

            # Смена статуса (online / away / dnd)
            elif msg_type == "status":
                new_status = data.get("status", "online")
                if new_status in ("online", "away", "dnd", "offline"):
                    _set_status(user_id, new_status)
                    await manager.broadcast_all({
                        "type": "status",
                        "user_id": user_id,
                        "status": new_status,
                    })

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws, user_id)
        _set_status(user_id, "offline")
        await manager.broadcast_all({
            "type": "status",
            "user_id": user_id,
            "status": "offline",
        })
        logger.info(f"WS closed: user_id={user_id}")


# ── Запуск ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    logger.info("Запуск Nexus на http://localhost:8000")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

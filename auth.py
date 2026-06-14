import logging
import re
import hashlib
import hmac
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy.orm import Session

from database import get_db
from models import User, Achievement

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["auth"])

EMAIL_RE = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')
SECRET = "nexus_secret_2026"


def hash_password(password: str) -> str:
    return hashlib.sha256(f"{password}{SECRET}".encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_password(password), hashed)

def make_token(user_id: int, email: str) -> str:
    raw = f"{user_id}:{email}:{SECRET}"
    return hashlib.sha256(raw.encode()).hexdigest()

def get_current_user(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    token = authorization.split(" ", 1)[1]
    for user in db.query(User).all():
        if make_token(user.id, user.email) == token:
            return user
    raise HTTPException(status_code=401, detail="Неверный токен")

def serialize_user(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "about": u.about,
        "birth_date": u.birth_date,
        "avatar_url": u.avatar_url,
        "banner_url": getattr(u, "banner_url", None),
        "status": getattr(u, "status", "offline"),
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }

@router.post("/register", status_code=201)
def register(data: dict, db: Session = Depends(get_db)):
    username = data.get("username", "").strip()
    email    = data.get("email", "").strip()
    password = data.get("password", "")
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Некорректный email адрес")
    if len(username) < 2:
        raise HTTPException(400, "Имя пользователя слишком короткое")
    if len(password) < 4:
        raise HTTPException(400, "Пароль должен быть не менее 4 символов")
    if db.query(User).filter_by(email=email).first():
        raise HTTPException(400, "Этот email уже зарегистрирован")
    if db.query(User).filter_by(username=username).first():
        raise HTTPException(400, "Имя пользователя уже занято")
    user = User(username=username, email=email,
                password=hash_password(password), status="offline")
    db.add(user); db.commit(); db.refresh(user)
    db.add(Achievement(user_id=user.id, code="first_day", title="Первый день в Nexus"))
    db.commit()
    logger.info(f"Зарегистрирован: {user.username}")
    return {"message": "Аккаунт успешно создан"}

@router.post("/login")
def login(data: dict, db: Session = Depends(get_db)):
    email    = data.get("email", "").strip()
    password = data.get("password", "")
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Некорректный email адрес")
    user = db.query(User).filter_by(email=email).first()
    if not user or not verify_password(password, user.password):
        raise HTTPException(401, "Неверный email или пароль")
    user.status    = "online"
    user.last_seen = datetime.utcnow()
    db.commit()
    token = make_token(user.id, user.email)
    logger.info(f"Вход: {user.username}")
    return {"token": token, "user": serialize_user(user)}

@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return serialize_user(current_user)

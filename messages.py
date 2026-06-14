import os
import uuid
import logging
import aiofiles
import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, joinedload
from typing import Optional

from database import get_db
from models import Message, Reaction, User, Chat, Achievement
from auth import get_current_user
from ws import manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["messages"])

UPLOAD_DIR = "./uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXT = {
    ".jpg": "image",  ".jpeg": "image", ".png": "image",
    ".gif": "image",  ".bmp": "image",  ".webp": "image",
    ".pdf": "document", ".doc": "document", ".docx": "document",
    ".xls": "document", ".xlsx": "document", ".ppt": "document", ".pptx": "document",
    ".txt": "document", ".csv": "document",
    ".zip": "archive", ".rar": "archive", ".7z": "archive",
    ".mp3": "audio",  ".mp4": "video",
}
MAX_SIZE = 20 * 1024 * 1024


# ── Сериализация ──────────────────────────────────────────────────────────────
def serialize_user(u: User) -> dict:
    return {
        "id": u.id, "username": u.username, "email": u.email,
        "about": u.about, "birth_date": u.birth_date,
        "avatar_url": u.avatar_url, "banner_url": u.banner_url,
        "status": u.status,
    }


def serialize_message(msg: Message) -> dict:
    return {
        "id": msg.id,
        "chat_id": msg.chat_id,
        "sender": serialize_user(msg.sender),
        "text": msg.text,
        "attachment_url": msg.attachment_url,
        "attachment_name": msg.attachment_name,
        "attachment_type": msg.attachment_type,
        "reply_to_id": msg.reply_to_id,
        "is_pinned": msg.is_pinned,
        "is_read": msg.is_read,
        "timestamp": msg.timestamp.isoformat(),
        "reactions": [
            {"emoji": r.emoji, "username": r.user.username}
            for r in msg.reactions
        ],
    }


def serialize_chat(chat: Chat, me: User) -> dict:
    name = chat.name
    avatar = chat.avatar_url
    if not chat.is_group:
        other = next((m for m in chat.members if m.id != me.id), None)
        if other:
            name = other.username
            avatar = other.avatar_url
    return {
        "id": chat.id,
        "name": name,
        "is_group": chat.is_group,
        "avatar": avatar,
        "description": chat.description,
        "created_by": chat.created_by,
        "members": [serialize_user(m) for m in chat.members],
    }


# ── Достижения ────────────────────────────────────────────────────────────────
def maybe_unlock(db: Session, user: User, code: str, title: str):
    exists = db.query(Achievement).filter_by(user_id=user.id, code=code).first()
    if not exists:
        db.add(Achievement(user_id=user.id, code=code, title=title))
        db.commit()


# ── Поиск пользователей ───────────────────────────────────────────────────────
@router.get("/users/search")
def search_users(
    q: str = "",
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    if len(q) < 1:
        return []
    users = (
        db.query(User)
        .filter(User.username.ilike(f"%{q}%"), User.id != me.id)
        .limit(10)
        .all()
    )
    return [serialize_user(u) for u in users]


@router.get("/users/{user_id}")
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(404, "Пользователь не найден")
    data = serialize_user(user)
    data["message_count"] = db.query(Message).filter_by(sender_id=user_id).count()
    data["achievements"] = [
        {"code": a.code, "title": a.title, "unlocked_at": a.unlocked_at.isoformat()}
        for a in user.achievements
    ]
    return data


# ── Профиль ───────────────────────────────────────────────────────────────────
@router.put("/profile")
async def update_profile(
    username:   Optional[str]        = Form(None),
    about:      Optional[str]        = Form(None),
    birth_date: Optional[str]        = Form(None),
    avatar:     Optional[UploadFile] = File(None),
    banner:     Optional[UploadFile] = File(None),
    db:         Session              = Depends(get_db),
    me:         User                 = Depends(get_current_user),
):
    if username and username.strip() and username.strip() != me.username:
        if db.query(User).filter_by(username=username.strip()).first():
            raise HTTPException(400, "Имя уже занято")
        me.username = username.strip()
    if about is not None:
        me.about = about
    if birth_date is not None:
        me.birth_date = birth_date

    for field, upload, attr in [("avatar", avatar, "avatar_url"), ("banner", banner, "banner_url")]:
        if upload and upload.filename:
            _, ext = os.path.splitext(upload.filename.lower())
            if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                content = await upload.read()
                fname = f"{field}_{me.id}{ext}"
                async with aiofiles.open(os.path.join(UPLOAD_DIR, fname), "wb") as f:
                    await f.write(content)
                setattr(me, attr, f"/api/uploads/{fname}")

    db.commit()
    db.refresh(me)
    return serialize_user(me)


# ── Чаты ─────────────────────────────────────────────────────────────────────
@router.get("/chats")
def get_chats(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    chats = (
        db.query(Chat)
        .options(joinedload(Chat.members))
        .filter(Chat.members.any(User.id == me.id))
        .all()
    )
    return [serialize_chat(c, me) for c in chats]


@router.post("/chats/direct")
def create_direct(
    user_id: int,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    other = db.query(User).filter_by(id=user_id).first()
    if not other:
        raise HTTPException(404, "Пользователь не найден")

    my_chats = (
        db.query(Chat)
        .options(joinedload(Chat.members))
        .filter(Chat.members.any(User.id == me.id), Chat.is_group == False)
        .all()
    )
    for c in my_chats:
        if {m.id for m in c.members} == {me.id, other.id}:
            return serialize_chat(c, me)

    chat = Chat(is_group=False, created_by=me.id)
    chat.members = [me, other]
    db.add(chat)
    db.commit()
    db.refresh(chat)
    chat = (
        db.query(Chat)
        .options(joinedload(Chat.members))
        .filter_by(id=chat.id)
        .first()
    )
    return serialize_chat(chat, me)


@router.post("/chats/group")
def create_group(
    name:       str = Form(...),
    member_ids: str = Form(...),
    db:         Session = Depends(get_db),
    me:         User    = Depends(get_current_user),
):
    ids = json.loads(member_ids)
    members = db.query(User).filter(User.id.in_(ids)).all()
    chat = Chat(name=name, is_group=True, created_by=me.id)
    chat.members = [me] + members
    db.add(chat)
    db.commit()
    db.refresh(chat)

    # Достижение создателю группы
    maybe_unlock(db, me, "group_creator", "Создатель группы")

    chat = (
        db.query(Chat)
        .options(joinedload(Chat.members))
        .filter_by(id=chat.id)
        .first()
    )
    return serialize_chat(chat, me)


@router.put("/chats/{chat_id}/name")
def rename_chat(
    chat_id: int,
    name: str = Form(...),
    db: Session = Depends(get_db),
    me: User    = Depends(get_current_user),
):
    chat = (
        db.query(Chat)
        .options(joinedload(Chat.members))
        .filter_by(id=chat_id)
        .first()
    )
    if not chat or not chat.is_group or me not in chat.members:
        raise HTTPException(403, "Нет доступа")
    chat.name = name
    db.commit()
    return {"ok": True}


# ── Сообщения ─────────────────────────────────────────────────────────────────
@router.get("/chats/{chat_id}/messages")
def get_messages(
    chat_id: int,
    limit: int = 100,
    db: Session = Depends(get_db),
    me: User    = Depends(get_current_user),
):
    chat = (
        db.query(Chat)
        .options(joinedload(Chat.members))
        .filter_by(id=chat_id)
        .first()
    )
    if not chat or me not in chat.members:
        raise HTTPException(403, "Нет доступа")

    msgs = (
        db.query(Message)
        .options(
            joinedload(Message.sender),
            joinedload(Message.reactions).joinedload(Reaction.user),
        )
        .filter_by(chat_id=chat_id)
        .order_by(Message.timestamp.asc())
        .limit(limit)
        .all()
    )
    return [serialize_message(m) for m in msgs]


@router.post("/chats/{chat_id}/messages")
async def send_message(
    chat_id:         int,
    text:            Optional[str] = Form(None),
    attachment_url:  Optional[str] = Form(None),
    attachment_name: Optional[str] = Form(None),
    attachment_type: Optional[str] = Form(None),
    reply_to_id:     Optional[int] = Form(None),
    db:              Session        = Depends(get_db),
    me:              User           = Depends(get_current_user),
):
    chat = (
        db.query(Chat)
        .options(joinedload(Chat.members))
        .filter_by(id=chat_id)
        .first()
    )
    if not chat or me not in chat.members:
        raise HTTPException(403, "Нет доступа")
    if not text and not attachment_url:
        raise HTTPException(400, "Пустое сообщение")

    msg = Message(
        chat_id=chat_id, sender_id=me.id,
        text=text,
        attachment_url=attachment_url,
        attachment_name=attachment_name,
        attachment_type=attachment_type,
        reply_to_id=reply_to_id,
    )
    db.add(msg)
    db.commit()

    msg = (
        db.query(Message)
        .options(
            joinedload(Message.sender),
            joinedload(Message.reactions).joinedload(Reaction.user),
        )
        .filter_by(id=msg.id)
        .first()
    )

    # Достижения по количеству сообщений
    total = db.query(Message).filter_by(sender_id=me.id).count()
    if total >= 1:
        maybe_unlock(db, me, "first_message", "Первое сообщение")
    if total >= 100:
        maybe_unlock(db, me, "msg_100", "100 сообщений")
    if total >= 1000:
        maybe_unlock(db, me, "msg_1000", "1000 сообщений")

    serialized = serialize_message(msg)
    member_ids = [m.id for m in chat.members]

    # WebSocket: рассылаем всем участникам кроме отправителя
    await manager.broadcast_chat(
        member_ids,
        {"type": "message", "chat_id": chat_id, "message": serialized},
        exclude=me.id,
    )

    return serialized


# ── Реакции ───────────────────────────────────────────────────────────────────
@router.post("/messages/{message_id}/react")
async def react(
    message_id: int,
    data: dict,
    db: Session = Depends(get_db),
    me: User    = Depends(get_current_user),
):
    emoji = data.get("emoji", "")
    existing = db.query(Reaction).filter_by(
        message_id=message_id, user_id=me.id, emoji=emoji
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
        action = "removed"
    else:
        db.query(Reaction).filter_by(message_id=message_id, user_id=me.id).delete()
        db.add(Reaction(message_id=message_id, user_id=me.id, emoji=emoji))
        db.commit()
        action = "added"

    # Уведомить участников чата об изменении реакций
    msg = db.query(Message).filter_by(id=message_id).first()
    if msg:
        chat = db.query(Chat).options(joinedload(Chat.members)).filter_by(id=msg.chat_id).first()
        if chat:
            member_ids = [m.id for m in chat.members]
            await manager.broadcast_chat(
                member_ids,
                {"type": "reaction", "chat_id": msg.chat_id, "message_id": message_id},
            )

    return {"action": action}


# ── Закреплённые сообщения ────────────────────────────────────────────────────
@router.post("/messages/{message_id}/pin")
def pin_message(
    message_id: int,
    db: Session = Depends(get_db),
    me: User    = Depends(get_current_user),
):
    msg = db.query(Message).filter_by(id=message_id).first()
    if not msg:
        raise HTTPException(404, "Сообщение не найдено")
    chat = db.query(Chat).options(joinedload(Chat.members)).filter_by(id=msg.chat_id).first()
    if not chat or me not in chat.members:
        raise HTTPException(403, "Нет доступа")
    msg.is_pinned = not msg.is_pinned
    db.commit()
    return {"is_pinned": msg.is_pinned}


# ── Файлы ─────────────────────────────────────────────────────────────────────
@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    me:   User       = Depends(get_current_user),
):
    _, ext = os.path.splitext(file.filename.lower())
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, "Тип файла не поддерживается")
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "Файл слишком большой (макс. 20 МБ)")
    unique = f"{uuid.uuid4().hex}{ext}"
    async with aiofiles.open(os.path.join(UPLOAD_DIR, unique), "wb") as f:
        await f.write(content)
    return {
        "file_url": f"/api/uploads/{unique}",
        "original_name": file.filename,
        "file_type": ALLOWED_EXT[ext],
    }


@router.get("/uploads/{filename}")
def get_file(filename: str):
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(404, "Файл не найден")
    return FileResponse(path)


# ── Аватар группы ─────────────────────────────────────────────────────────────
@router.put("/chats/{chat_id}/avatar")
async def update_group_avatar(
    chat_id: int,
    avatar:  UploadFile = File(...),
    db:      Session    = Depends(get_db),
    me:      User       = Depends(get_current_user),
):
    chat = db.query(Chat).options(joinedload(Chat.members)).filter_by(id=chat_id).first()
    if not chat or not chat.is_group or me not in chat.members:
        raise HTTPException(403, "Нет доступа")
    _, ext = os.path.splitext(avatar.filename.lower())
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        raise HTTPException(400, "Неподдерживаемый формат")
    content = await avatar.read()
    fname = f"group_{chat_id}{ext}"
    async with aiofiles.open(os.path.join(UPLOAD_DIR, fname), "wb") as f:
        await f.write(content)
    chat.avatar_url = f"/api/uploads/{fname}"
    db.commit()
    return {"avatar_url": chat.avatar_url}


# ── Пометить сообщения прочитанными ──────────────────────────────────────────
@router.post("/chats/{chat_id}/read")
def mark_read(
    chat_id: int,
    db: Session = Depends(get_db),
    me: User    = Depends(get_current_user),
):
    chat = db.query(Chat).options(joinedload(Chat.members)).filter_by(id=chat_id).first()
    if not chat or me not in chat.members:
        raise HTTPException(403, "Нет доступа")
    # Помечаем все входящие сообщения как прочитанные
    db.query(Message).filter(
        Message.chat_id == chat_id,
        Message.sender_id != me.id,
        Message.is_read == False,
    ).update({"is_read": True})
    db.commit()
    return {"ok": True}

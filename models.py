from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Table
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

# ── Many-to-many: участники чата ──────────────────────────────────────────────
chat_members = Table(
    "chat_members", Base.metadata,
    Column("chat_id", Integer, ForeignKey("chats.id"), primary_key=True),
    Column("user_id",  Integer, ForeignKey("users.id"),  primary_key=True),
)

# ── Many-to-many: друзья ──────────────────────────────────────────────────────
friendships = Table(
    "friendships", Base.metadata,
    Column("user_id",   Integer, ForeignKey("users.id"), primary_key=True),
    Column("friend_id", Integer, ForeignKey("users.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id           = Column(Integer, primary_key=True, index=True)
    username     = Column(String(50),  unique=True, nullable=False)
    email        = Column(String(100), unique=True, nullable=False)
    password     = Column(String(255), nullable=False)
    about        = Column(Text,        nullable=True)
    birth_date   = Column(String(20),  nullable=True)
    avatar_url   = Column(String(500), nullable=True)
    banner_url   = Column(String(500), nullable=True)
    # online / away / dnd / offline
    status       = Column(String(20),  default="offline", nullable=False)
    last_seen    = Column(DateTime,    default=datetime.utcnow)
    created_at   = Column(DateTime,    default=datetime.utcnow)

    messages    = relationship("Message",  back_populates="sender")
    reactions   = relationship("Reaction", back_populates="user")
    chats       = relationship("Chat", secondary=chat_members, back_populates="members")
    friends     = relationship(
        "User", secondary=friendships,
        primaryjoin=id == friendships.c.user_id,
        secondaryjoin=id == friendships.c.friend_id,
    )
    sent_requests     = relationship("FriendRequest", foreign_keys="FriendRequest.from_id", back_populates="sender")
    received_requests = relationship("FriendRequest", foreign_keys="FriendRequest.to_id",   back_populates="receiver")
    achievements      = relationship("Achievement",   back_populates="user")


class FriendRequest(Base):
    __tablename__ = "friend_requests"

    id        = Column(Integer, primary_key=True, index=True)
    from_id   = Column(Integer, ForeignKey("users.id"), nullable=False)
    to_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    # pending / accepted / declined
    status    = Column(String(20), default="pending", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    sender   = relationship("User", foreign_keys=[from_id], back_populates="sent_requests")
    receiver = relationship("User", foreign_keys=[to_id],   back_populates="received_requests")


class Achievement(Base):
    __tablename__ = "achievements"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    code       = Column(String(50),  nullable=False)   # first_day, msg_100, group_creator …
    title      = Column(String(100), nullable=False)
    unlocked_at = Column(DateTime,  default=datetime.utcnow)

    user = relationship("User", back_populates="achievements")


class Chat(Base):
    __tablename__ = "chats"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(100), nullable=True)
    is_group   = Column(Boolean,     default=False)
    avatar_url = Column(String(500), nullable=True)
    description = Column(Text,       nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    members  = relationship("User",    secondary=chat_members, back_populates="chats")
    messages = relationship("Message", back_populates="chat",  cascade="all, delete")


class Message(Base):
    __tablename__ = "messages"

    id              = Column(Integer, primary_key=True, index=True)
    chat_id         = Column(Integer, ForeignKey("chats.id"),    nullable=False)
    sender_id       = Column(Integer, ForeignKey("users.id"),    nullable=False)
    text            = Column(Text,    nullable=True)
    attachment_url  = Column(String(500), nullable=True)
    attachment_name = Column(String(255), nullable=True)
    attachment_type = Column(String(50),  nullable=True)
    reply_to_id     = Column(Integer, ForeignKey("messages.id"), nullable=True)
    is_pinned       = Column(Boolean, default=False)
    is_read         = Column(Boolean, default=False)
    timestamp       = Column(DateTime, default=datetime.utcnow)

    chat      = relationship("Chat",    back_populates="messages")
    sender    = relationship("User",    back_populates="messages")
    reactions = relationship("Reaction", back_populates="message", cascade="all, delete")
    reply_to  = relationship("Message",  remote_side="Message.id", foreign_keys=[reply_to_id])


class Reaction(Base):
    __tablename__ = "reactions"

    id         = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False)
    user_id    = Column(Integer, ForeignKey("users.id"),    nullable=False)
    emoji      = Column(String(10), nullable=False)

    message = relationship("Message", back_populates="reactions")
    user    = relationship("User",    back_populates="reactions")

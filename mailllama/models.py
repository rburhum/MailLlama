"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))  # gmail_api | imap
    email: Mapped[str] = mapped_column(String(320), unique=True)
    # Fernet-encrypted OAuth token blob (Gmail) or placeholder (IMAP).
    oauth_blob: Mapped[str | None] = mapped_column(Text, nullable=True)
    imap_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Gmail historyId (str) or IMAP UIDNEXT (str). Opaque per-provider.
    cursor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Message(Base):
    __tablename__ = "message"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    provider_msg_id: Mapped[str] = mapped_column(String(255))
    thread_id: Mapped[str] = mapped_column(String(255), index=True)

    from_addr: Mapped[str] = mapped_column(String(320), index=True)
    from_name: Mapped[str | None] = mapped_column(String(320), nullable=True)
    to_addrs: Mapped[list[str]] = mapped_column(JSON, default=list)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)

    list_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    list_unsub_http: Mapped[str | None] = mapped_column(Text, nullable=True)
    list_unsub_mailto: Mapped[str | None] = mapped_column(String(320), nullable=True)
    list_unsub_one_click: Mapped[bool] = mapped_column(Boolean, default=False)

    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    raw_headers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    classifications: Mapped[list["Classification"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("account_id", "provider_msg_id", name="uq_message_account_msg"),
        Index("ix_message_account_date", "account_id", "date"),
    )


class Thread(Base):
    __tablename__ = "thread"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    thread_id: Mapped[str] = mapped_column(String(255))
    last_message_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    user_has_replied: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    __table_args__ = (
        UniqueConstraint("account_id", "thread_id", name="uq_thread_account_thread"),
    )


class Classification(Base):
    __tablename__ = "classification"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("message.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String(16))  # sender | message
    label: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    message: Mapped[Message] = relationship(back_populates="classifications")


class Sender(Base):
    __tablename__ = "sender"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    normalized_addr: Mapped[str] = mapped_column(String(320), index=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    total_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    latest_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latest_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    classified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("account_id", "normalized_addr", name="uq_sender_account_addr"),
    )


class Rule(Base):
    __tablename__ = "rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))  # blacklist | whitelist
    match_kind: Mapped[str] = mapped_column(String(16))  # email | domain | header | regex
    pattern: Mapped[str] = mapped_column(String(1024))
    action: Mapped[str] = mapped_column(String(32))  # ignore | auto_archive | auto_trash
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ActionLog(Base):
    __tablename__ = "action_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("message.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(32))
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    performed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    undoable_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TaskRecord(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KVCache(Base):
    """Fallback key/value cache when Redis is unavailable."""

    __tablename__ = "kv_cache"

    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

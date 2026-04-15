"""Size reports — biggest messages and senders."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..models import Message, Sender


@dataclass
class BySender:
    addr: str
    domain: str
    message_count: int
    total_size_bytes: int


@dataclass
class BigMessage:
    id: int
    provider_msg_id: str
    from_addr: str
    subject: str | None
    size_bytes: int


def top_senders_by_size(
    session: Session, account_id: int, *, limit: int = 50
) -> list[BySender]:
    rows = session.execute(
        select(
            Sender.normalized_addr, Sender.domain, Sender.message_count, Sender.total_size_bytes
        )
        .where(Sender.account_id == account_id)
        .order_by(desc(Sender.total_size_bytes))
        .limit(limit)
    ).all()
    return [
        BySender(
            addr=r.normalized_addr,
            domain=r.domain,
            message_count=r.message_count,
            total_size_bytes=r.total_size_bytes,
        )
        for r in rows
    ]


def top_messages_by_size(
    session: Session, account_id: int, *, limit: int = 100
) -> list[BigMessage]:
    rows = session.execute(
        select(Message.id, Message.provider_msg_id, Message.from_addr, Message.subject, Message.size_bytes)
        .where(Message.account_id == account_id)
        .order_by(desc(Message.size_bytes))
        .limit(limit)
    ).all()
    return [
        BigMessage(
            id=r.id,
            provider_msg_id=r.provider_msg_id,
            from_addr=r.from_addr,
            subject=r.subject,
            size_bytes=r.size_bytes,
        )
        for r in rows
    ]


def inbox_total_size(session: Session, account_id: int) -> int:
    return session.scalar(
        select(func.coalesce(func.sum(Message.size_bytes), 0)).where(
            Message.account_id == account_id
        )
    ) or 0

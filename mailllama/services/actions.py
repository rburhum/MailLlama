"""Batch mail actions (archive / trash / label) with safety rails."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import ActionLog, Message
from ..providers.base import MailProvider


def _fetch_provider_ids(session: Session, account_id: int, message_ids: list[int]) -> list[Message]:
    return list(
        session.scalars(
            select(Message).where(
                Message.account_id == account_id, Message.id.in_(message_ids)
            )
        ).all()
    )


def _log(session: Session, account_id: int, messages: list[Message], action: str, result: str, undo_days: int = 0) -> None:
    undo_until = datetime.utcnow() + timedelta(days=undo_days) if undo_days else None
    for m in messages:
        session.add(
            ActionLog(
                account_id=account_id,
                message_id=m.id,
                action=action,
                result=result,
                undoable_until=undo_until,
            )
        )


def batch_archive(
    session: Session, account_id: int, message_ids: list[int], provider: MailProvider
) -> int:
    settings = get_settings()
    messages = _fetch_provider_ids(session, account_id, message_ids)
    provider_ids = [m.provider_msg_id for m in messages]
    if settings.dry_run:
        _log(session, account_id, messages, "archive", "dry run")
        return len(messages)
    provider.batch_archive(provider_ids)
    _log(session, account_id, messages, "archive", "ok")
    # Reflect locally: drop INBOX label.
    for m in messages:
        m.labels = [lbl for lbl in (m.labels or []) if lbl != "INBOX"]
    return len(messages)


def batch_trash(
    session: Session, account_id: int, message_ids: list[int], provider: MailProvider
) -> int:
    settings = get_settings()
    messages = _fetch_provider_ids(session, account_id, message_ids)
    provider_ids = [m.provider_msg_id for m in messages]
    if settings.dry_run:
        _log(session, account_id, messages, "trash", "dry run", undo_days=30)
        return len(messages)
    provider.batch_trash(provider_ids)
    _log(session, account_id, messages, "trash", "ok", undo_days=30)
    for m in messages:
        m.labels = [lbl for lbl in (m.labels or []) if lbl != "INBOX"] + ["TRASH"]
    return len(messages)


def batch_move(
    session: Session,
    account_id: int,
    message_ids: list[int],
    provider: MailProvider,
    *,
    target_label: str,
) -> int:
    settings = get_settings()
    messages = _fetch_provider_ids(session, account_id, message_ids)
    provider_ids = [m.provider_msg_id for m in messages]
    if settings.dry_run:
        _log(session, account_id, messages, f"move:{target_label}", "dry run")
        return len(messages)
    provider.batch_modify_labels(provider_ids, add=[target_label], remove=["INBOX"])
    _log(session, account_id, messages, f"move:{target_label}", "ok")
    for m in messages:
        m.labels = [lbl for lbl in (m.labels or []) if lbl != "INBOX"] + [target_label]
    return len(messages)


def batch_trash_by_sender(
    session: Session,
    account_id: int,
    addr: str,
    provider: MailProvider,
    *,
    only_unreplied: bool = False,
) -> int:
    q = select(Message).where(
        Message.account_id == account_id,
        Message.from_addr == addr.lower(),
    )
    # "only unreplied" filters to threads the user never replied to.
    if only_unreplied:
        from ..models import Thread

        q = q.join(Thread, Thread.thread_id == Message.thread_id).where(
            Thread.account_id == account_id,
            (Thread.user_has_replied.is_(False)) | (Thread.user_has_replied.is_(None)),
        )
    ids = [m.id for m in session.scalars(q).all()]
    if not ids:
        return 0
    return batch_trash(session, account_id, ids, provider)

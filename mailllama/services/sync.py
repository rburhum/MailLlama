"""Sync: pull messages from a provider into the local DB."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, Message, Sender, Thread
from ..providers.base import MailMessage, MailProvider
from ..tasks.runner import TaskHandle


def _upsert_message(session: Session, account_id: int, m: MailMessage) -> Message:
    existing = session.scalar(
        select(Message).where(
            Message.account_id == account_id,
            Message.provider_msg_id == m.provider_msg_id,
        )
    )
    if existing is None:
        row = Message(
            account_id=account_id,
            provider_msg_id=m.provider_msg_id,
            thread_id=m.thread_id,
            from_addr=m.from_addr,
            from_name=m.from_name,
            to_addrs=m.to_addrs,
            subject=m.subject,
            date=m.date,
            size_bytes=m.size_bytes,
            snippet=m.snippet,
            list_id=m.list_id,
            list_unsub_http=m.list_unsub_http,
            list_unsub_mailto=m.list_unsub_mailto,
            list_unsub_one_click=m.list_unsub_one_click,
            is_read=m.is_read,
            labels=m.labels,
            raw_headers=m.raw_headers,
        )
        session.add(row)
        session.flush()
        return row
    existing.is_read = m.is_read
    existing.labels = m.labels
    return existing


def _upsert_thread(session: Session, account_id: int, thread_id: str, date: datetime | None) -> None:
    t = session.scalar(
        select(Thread).where(Thread.account_id == account_id, Thread.thread_id == thread_id)
    )
    if t is None:
        session.add(
            Thread(
                account_id=account_id,
                thread_id=thread_id,
                last_message_date=date,
                message_count=1,
            )
        )
    else:
        t.message_count += 1
        if date and (t.last_message_date is None or date > t.last_message_date):
            t.last_message_date = date


def _upsert_sender(session: Session, account_id: int, m: MailMessage) -> None:
    addr = m.from_addr.strip().lower()
    if not addr:
        return
    domain = addr.split("@", 1)[-1] if "@" in addr else addr
    s = session.scalar(
        select(Sender).where(
            Sender.account_id == account_id, Sender.normalized_addr == addr
        )
    )
    if s is None:
        session.add(
            Sender(
                account_id=account_id,
                normalized_addr=addr,
                domain=domain,
                first_seen=m.date,
                last_seen=m.date,
                message_count=1,
                total_size_bytes=m.size_bytes,
            )
        )
    else:
        s.message_count += 1
        s.total_size_bytes += m.size_bytes
        if m.date:
            if s.first_seen is None or m.date < s.first_seen:
                s.first_seen = m.date
            if s.last_seen is None or m.date > s.last_seen:
                s.last_seen = m.date


def sync_account(
    session: Session,
    account: Account,
    provider: MailProvider,
    *,
    handle: TaskHandle | None = None,
    max_results: int = 5000,
) -> int:
    messages, new_cursor = provider.list_since(account.cursor, max_results=max_results)
    count = 0
    for m in messages:
        _upsert_message(session, account.id, m)
        _upsert_thread(session, account.id, m.thread_id, m.date)
        _upsert_sender(session, account.id, m)
        count += 1
        if count % 50 == 0:
            session.commit()  # release write lock so other tasks can proceed
            if handle:
                handle.update(session=session, progress=count, message=f"Synced {count} messages")
    account.cursor = new_cursor
    session.commit()
    if handle:
        handle.update(session=session, progress=count, total=count, message=f"Synced {count} messages")
    return count

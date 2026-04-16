"""Thread interaction detection: did the user ever reply?"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..models import Account, Message, Sender, Thread
from ..providers.base import MailProvider
from ..tasks.runner import TaskHandle


def compute_interactions(
    session: Session,
    account: Account,
    provider: MailProvider,
    *,
    handle: TaskHandle | None = None,
    only_unknown: bool = True,
) -> int:
    q = select(Thread).where(Thread.account_id == account.id)
    if only_unknown:
        q = q.where(Thread.user_has_replied.is_(None))
    threads = list(session.scalars(q).all())

    if handle:
        handle.update(session=session, total=len(threads), message=f"Checking {len(threads)} threads")

    for i, t in enumerate(threads, 1):
        try:
            sent_ids = provider.sent_message_ids_in_thread(t.thread_id)
            t.user_has_replied = bool(sent_ids)
        except Exception:  # noqa: BLE001
            t.user_has_replied = None
        if i % 25 == 0:
            session.commit()
            if handle:
                handle.update(session=session, progress=i)

    session.commit()

    # Roll up reply counts per sender.
    replied = (
        select(Message.from_addr, func.count(Message.id).label("replies"))
        .join(Thread, Thread.thread_id == Message.thread_id)
        .where(
            Message.account_id == account.id,
            Thread.account_id == account.id,
            Thread.user_has_replied.is_(True),
        )
        .group_by(Message.from_addr)
    )
    reply_map = {row.from_addr: row.replies for row in session.execute(replied)}

    # Reset all to 0 then update known ones.
    session.execute(
        update(Sender).where(Sender.account_id == account.id).values(reply_count=0)
    )
    for addr, count in reply_map.items():
        session.execute(
            update(Sender)
            .where(Sender.account_id == account.id, Sender.normalized_addr == addr)
            .values(reply_count=count)
        )
    return len(threads)

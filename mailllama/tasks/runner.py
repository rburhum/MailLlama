"""In-process asyncio task runner with DB-backed progress.

A task is represented by a row in the ``task`` table. The web UI polls that
row via ``/tasks/{id}`` to show progress.

Redis/Huey is deliberately NOT required. If ``REDIS_URL`` is set, you can
still use this runner — tasks just aren't durable across restarts.
Proper Huey integration is left for a follow-up (see ``plan`` notes).
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..db import SessionLocal, session_scope
from ..models import TaskRecord

log = logging.getLogger(__name__)

TaskFn = Callable[["TaskHandle"], Awaitable[Any]]


class TaskHandle:
    """Handle given to a task function so it can report progress."""

    def __init__(self, task_id: int) -> None:
        self.task_id = task_id

    def update(
        self,
        *,
        progress: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        with session_scope() as session:
            rec = session.get(TaskRecord, self.task_id)
            if rec is None:
                return
            if progress is not None:
                rec.progress = progress
            if total is not None:
                rec.total = total
            if message is not None:
                rec.message = message


def create_task_record(kind: str, total: int = 0) -> int:
    with session_scope() as session:
        rec = TaskRecord(kind=kind, status="pending", total=total)
        session.add(rec)
        session.flush()
        return rec.id


def _mark(session: Session, task_id: int, **fields: Any) -> None:
    rec = session.get(TaskRecord, task_id)
    if rec is None:
        return
    for k, v in fields.items():
        setattr(rec, k, v)


async def _run(task_id: int, fn: TaskFn) -> None:
    handle = TaskHandle(task_id)
    with session_scope() as session:
        _mark(session, task_id, status="running", started_at=datetime.utcnow())
    try:
        await fn(handle)
    except Exception as exc:  # noqa: BLE001
        log.exception("Task %s failed", task_id)
        with session_scope() as session:
            _mark(
                session,
                task_id,
                status="failed",
                error=f"{exc}\n{traceback.format_exc()}",
                finished_at=datetime.utcnow(),
            )
        return
    with session_scope() as session:
        _mark(session, task_id, status="completed", finished_at=datetime.utcnow())


def submit(kind: str, fn: TaskFn, *, total: int = 0) -> int:
    """Submit a task for background execution. Returns task id."""
    task_id = create_task_record(kind, total=total)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (e.g. called from CLI) — run synchronously.
        asyncio.run(_run(task_id, fn))
        return task_id
    loop.create_task(_run(task_id, fn))
    return task_id


def get_task(task_id: int) -> TaskRecord | None:
    session = SessionLocal()
    try:
        return session.get(TaskRecord, task_id)
    finally:
        session.close()

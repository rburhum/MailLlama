"""Task runner: submits sync functions to a thread pool with DB-backed progress.

Each task is a row in the ``task`` table. The web UI streams progress via SSE.
Tasks run in a module-level ThreadPoolExecutor so they don't block the caller
regardless of whether it's an async route, a sync route (which FastAPI
dispatches to a worker thread), or the CLI.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..db import SessionLocal, session_scope
from ..models import TaskRecord

log = logging.getLogger(__name__)

# Module-level pool: independent of any asyncio loop, so submit() works from
# both sync routes (FastAPI worker thread) and CLI calls. max_workers caps
# the number of concurrent background tasks.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mailllama-task")

# Sync callable: receives a TaskHandle, does work, returns anything.
TaskFn = Callable[["TaskHandle"], Any]


class TaskHandle:
    """Handle given to a task function so it can report progress.

    When the caller already has an open SQLAlchemy session (which is the
    normal case inside ``sync_account`` / ``classify_senders`` / etc.),
    it should pass ``session=`` so the progress update happens on the
    *same* connection — avoids SQLite "database is locked" errors.
    """

    def __init__(self, task_id: int) -> None:
        self.task_id = task_id

    def update(
        self,
        *,
        progress: int | None = None,
        total: int | None = None,
        message: str | None = None,
        session: Session | None = None,
    ) -> None:
        if session is not None:
            self._apply(session, progress=progress, total=total, message=message)
        else:
            with session_scope() as s:
                self._apply(s, progress=progress, total=total, message=message)

        # Notify SSE subscribers (import here to avoid circular deps).
        from .events import notify

        notify(
            self.task_id,
            {
                "task_id": self.task_id,
                "progress": progress,
                "total": total,
                "message": message,
                "status": "running",
            },
        )

    def _apply(
        self,
        session: Session,
        *,
        progress: int | None,
        total: int | None,
        message: str | None,
    ) -> None:
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


def _run_sync(task_id: int, fn: TaskFn) -> None:
    """Run the task function synchronously (called in a thread)."""
    handle = TaskHandle(task_id)
    with session_scope() as session:
        _mark(session, task_id, status="running", started_at=datetime.utcnow())
    try:
        fn(handle)
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
        from .events import notify

        notify(task_id, {"task_id": task_id, "status": "failed", "message": str(exc)})
        return
    with session_scope() as session:
        _mark(session, task_id, status="completed", finished_at=datetime.utcnow())
    from .events import notify

    notify(task_id, {"task_id": task_id, "status": "completed"})


def submit(kind: str, fn: TaskFn, *, total: int = 0) -> int:
    """Submit a task for background execution. Returns task id immediately.

    Always uses the module-level ThreadPoolExecutor so this works from
    any caller: async routes, sync routes (which FastAPI dispatches to a
    worker thread with no event loop), and the CLI.
    """
    task_id = create_task_record(kind, total=total)
    _executor.submit(_run_sync, task_id, fn)
    return task_id


def get_task(task_id: int) -> TaskRecord | None:
    session = SessionLocal()
    try:
        return session.get(TaskRecord, task_id)
    finally:
        session.close()

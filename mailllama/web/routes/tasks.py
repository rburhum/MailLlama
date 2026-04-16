"""Task progress endpoints (HTML fragment + SSE stream)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ...models import TaskRecord
from ...tasks.events import subscribe, unsubscribe
from ..deps import get_db

router = APIRouter()


@router.get("/stream/{task_id}")
async def task_stream(task_id: int) -> EventSourceResponse:
    """SSE endpoint that pushes task progress as JSON events.

    Clients connect via ``new EventSource('/tasks/stream/123')``.
    Events arrive as the task runs; a final event with ``status``
    ``completed`` or ``failed`` signals the end of the stream.
    """
    q = subscribe(task_id)

    async def event_generator():
        try:
            while True:
                try:
                    # Check for messages from the worker thread (non-blocking).
                    data = q.get_nowait()
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(0.3)
                    continue
                yield {"data": json.dumps(data)}
                if data.get("status") in ("completed", "failed"):
                    break
        finally:
            unsubscribe(task_id, q)

    return EventSourceResponse(event_generator())


@router.get("/{task_id}", response_class=HTMLResponse)
def task_fragment(
    task_id: int,
    request: Request,
    session: Session = Depends(get_db),
) -> HTMLResponse:
    from ..app import get_templates

    rec = session.get(TaskRecord, task_id)
    if rec is None:
        raise HTTPException(404)
    return get_templates().TemplateResponse(
        request, "_task.html", {"task": rec}
    )


@router.get("/", response_class=HTMLResponse)
def task_list(
    request: Request,
    session: Session = Depends(get_db),
) -> HTMLResponse:
    from ..app import get_templates

    tasks = list(
        session.scalars(select(TaskRecord).order_by(desc(TaskRecord.id)).limit(25)).all()
    )
    return get_templates().TemplateResponse(
        request, "tasks.html", {"tasks": tasks}
    )

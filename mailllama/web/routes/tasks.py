"""Task progress endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ...models import TaskRecord
from ..deps import get_db

router = APIRouter()


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

"""Dashboard route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import Account, Message, Sender, Thread
from ..deps import get_db, maybe_account

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_db),
              account: Account | None = Depends(maybe_account)):
    from ..app import get_templates

    stats = {}
    if account is not None:
        stats["messages"] = session.scalar(
            select(func.count(Message.id)).where(Message.account_id == account.id)
        )
        stats["threads"] = session.scalar(
            select(func.count(Thread.id)).where(Thread.account_id == account.id)
        )
        stats["senders"] = session.scalar(
            select(func.count(Sender.id)).where(Sender.account_id == account.id)
        )
        stats["inbox_size"] = session.scalar(
            select(func.coalesce(func.sum(Message.size_bytes), 0)).where(
                Message.account_id == account.id
            )
        )
        stats["classified_senders"] = session.scalar(
            select(func.count(Sender.id)).where(
                Sender.account_id == account.id, Sender.latest_label.isnot(None)
            )
        )
    return get_templates().TemplateResponse(
        request,
        "dashboard.html",
        {"account": account, "stats": stats},
    )

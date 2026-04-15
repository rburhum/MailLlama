"""Sender browsing / triage routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ...models import Account, Message, Sender
from ..deps import get_account, get_db

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def list_senders(
    request: Request,
    label: str | None = None,
    session: Session = Depends(get_db),
    account: Account = Depends(get_account),
):
    from ..app import get_templates

    q = (
        select(Sender)
        .where(Sender.account_id == account.id)
        .order_by(desc(Sender.message_count))
        .limit(200)
    )
    if label:
        q = q.where(Sender.latest_label == label)
    senders = list(session.scalars(q).all())
    return get_templates().TemplateResponse(
        request,
        "senders.html",
        {"account": account, "senders": senders, "label": label},
    )


@router.get("/{sender_id}", response_class=HTMLResponse)
def sender_detail(
    sender_id: int,
    request: Request,
    session: Session = Depends(get_db),
    account: Account = Depends(get_account),
):
    from ..app import get_templates

    sender = session.get(Sender, sender_id)
    if sender is None or sender.account_id != account.id:
        return HTMLResponse("not found", status_code=404)
    messages = list(
        session.scalars(
            select(Message)
            .where(
                Message.account_id == account.id,
                Message.from_addr == sender.normalized_addr,
            )
            .order_by(desc(Message.date))
            .limit(100)
        ).all()
    )
    return get_templates().TemplateResponse(
        request,
        "sender_detail.html",
        {"account": account, "sender": sender, "messages": messages},
    )

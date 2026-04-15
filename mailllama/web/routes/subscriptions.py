"""Subscriptions / newsletters / neglected mail."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ...models import Account, Sender
from ..deps import get_account, get_db

router = APIRouter()

# Labels that "count as" subscriptions for this view.
SUBSCRIPTION_LABELS = ("subscription", "newsletter", "promo")


@router.get("/", response_class=HTMLResponse)
def subscriptions(
    request: Request,
    tab: str = "all",
    session: Session = Depends(get_db),
    account: Account = Depends(get_account),
):
    from ..app import get_templates

    q = (
        select(Sender)
        .where(
            Sender.account_id == account.id,
            Sender.latest_label.in_(SUBSCRIPTION_LABELS),
        )
        .order_by(desc(Sender.message_count))
    )
    if tab == "untouched":
        q = q.where(Sender.reply_count == 0)
    senders = list(session.scalars(q.limit(300)).all())
    return get_templates().TemplateResponse(
        request,
        "subscriptions.html",
        {
            "account": account,
            "senders": senders,
            "tab": tab,
        },
    )

"""Size reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ...models import Account
from ...services.sizes import inbox_total_size, top_messages_by_size, top_senders_by_size
from ..deps import get_account, get_db

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def sizes(
    request: Request,
    session: Session = Depends(get_db),
    account: Account = Depends(get_account),
):
    from ..app import get_templates

    return get_templates().TemplateResponse(
        request,
        "sizes.html",
        {
            "account": account,
            "total": inbox_total_size(session, account.id),
            "by_sender": top_senders_by_size(session, account.id, limit=50),
            "big_messages": top_messages_by_size(session, account.id, limit=100),
        },
    )

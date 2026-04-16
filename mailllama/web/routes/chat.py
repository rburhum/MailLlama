"""Chat route: talk to your inbox via LLM."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...models import Account
from ...providers.factory import provider_for
from ...services.chat import process_message
from ..deps import get_account, get_db

router = APIRouter()
log = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


@router.get("/", response_class=HTMLResponse)
def chat_page(
    request: Request,
    account: Account = Depends(get_account),
):
    from ..app import get_templates

    return get_templates().TemplateResponse(
        request, "chat.html", {"account": account}
    )


@router.post("/message")
def chat_message(
    body: ChatRequest,
    session: Session = Depends(get_db),
    account: Account = Depends(get_account),
) -> JSONResponse:
    try:
        provider = provider_for(account)
        reply, history = process_message(
            session, account, provider, body.message, body.history or None,
        )
        return JSONResponse({"reply": reply, "history": history})
    except Exception as exc:  # noqa: BLE001
        log.exception("Chat error")
        return JSONResponse(
            {"reply": f"Error: {exc}", "history": body.history},
            status_code=500,
        )

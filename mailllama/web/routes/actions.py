"""Batch actions + sync/classify kickoff."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from ...db import session_scope
from ...models import Account, Message
from ...providers.factory import provider_for
from ...services import actions as actions_svc
from ...services.classify import classify_senders
from ...services.interaction import compute_interactions
from ...services.sync import sync_account
from ...services.unsubscribe import unsubscribe_message
from ...tasks.runner import submit
from ..deps import get_account, get_db

router = APIRouter()


@router.post("/sync")
def start_sync(
    max_messages: int = Form(500),
    account: Account = Depends(get_account),
) -> JSONResponse:
    acct_id = account.id
    limit = max(1, min(max_messages, 50000))

    async def run(handle):
        with session_scope() as s:
            a = s.get(Account, acct_id)
            p = provider_for(a)
            sync_account(s, a, p, handle=handle, max_results=limit)

    task_id = submit("sync", run)
    return JSONResponse({"task_id": task_id})


@router.post("/classify")
def start_classify(account: Account = Depends(get_account)) -> JSONResponse:
    acct_id = account.id

    async def run(handle):
        with session_scope() as s:
            a = s.get(Account, acct_id)
            classify_senders(s, a, handle=handle)

    task_id = submit("classify", run)
    return JSONResponse({"task_id": task_id})


@router.post("/interactions")
def start_interactions(account: Account = Depends(get_account)) -> JSONResponse:
    acct_id = account.id

    async def run(handle):
        with session_scope() as s:
            a = s.get(Account, acct_id)
            p = provider_for(a)
            compute_interactions(s, a, p, handle=handle)

    task_id = submit("interactions", run)
    return JSONResponse({"task_id": task_id})


@router.post("/archive")
def action_archive(
    ids: list[int] = Form(...),
    session: Session = Depends(get_db),
    account: Account = Depends(get_account),
) -> JSONResponse:
    p = provider_for(account)
    n = actions_svc.batch_archive(session, account.id, ids, p)
    session.commit()
    return JSONResponse({"archived": n})


@router.post("/trash")
def action_trash(
    ids: list[int] = Form(...),
    session: Session = Depends(get_db),
    account: Account = Depends(get_account),
) -> JSONResponse:
    p = provider_for(account)
    n = actions_svc.batch_trash(session, account.id, ids, p)
    session.commit()
    return JSONResponse({"trashed": n})


@router.post("/trash_by_sender")
def action_trash_by_sender(
    addr: str = Form(...),
    only_unreplied: bool = Form(False),
    session: Session = Depends(get_db),
    account: Account = Depends(get_account),
) -> JSONResponse:
    p = provider_for(account)
    n = actions_svc.batch_trash_by_sender(
        session, account.id, addr, p, only_unreplied=only_unreplied
    )
    session.commit()
    return JSONResponse({"trashed": n})


@router.post("/unsubscribe/{message_id}")
def action_unsubscribe(
    message_id: int,
    session: Session = Depends(get_db),
    account: Account = Depends(get_account),
) -> JSONResponse:
    msg = session.get(Message, message_id)
    if msg is None or msg.account_id != account.id:
        raise HTTPException(404)
    p = provider_for(account)
    result = unsubscribe_message(msg, p)
    return JSONResponse(
        {"method": result.method, "success": result.success, "details": result.details}
    )

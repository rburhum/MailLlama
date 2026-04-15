"""Blacklist / whitelist CRUD."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ...models import Account
from ...services import rules as rules_svc
from ..deps import get_account, get_db

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def list_rules(
    request: Request,
    session: Session = Depends(get_db),
    account: Account = Depends(get_account),
):
    from ..app import get_templates

    return get_templates().TemplateResponse(
        request,
        "rules.html",
        {
            "account": account,
            "rules": rules_svc.list_rules(session, account.id),
        },
    )


@router.post("/")
def create_rule(
    kind: str = Form(...),
    match_kind: str = Form(...),
    pattern: str = Form(...),
    action: str = Form("ignore"),
    notes: str | None = Form(None),
    session: Session = Depends(get_db),
    account: Account = Depends(get_account),
) -> RedirectResponse:
    rules_svc.add_rule(
        session,
        account_id=account.id,
        kind=kind,
        match_kind=match_kind,
        pattern=pattern,
        action=action,
        notes=notes,
    )
    session.commit()
    return RedirectResponse("/rules/", status_code=303)


@router.post("/{rule_id}/delete")
def delete_rule(
    rule_id: int,
    session: Session = Depends(get_db),
    account: Account = Depends(get_account),
) -> RedirectResponse:
    rules_svc.delete_rule(session, account.id, rule_id)
    session.commit()
    return RedirectResponse("/rules/", status_code=303)

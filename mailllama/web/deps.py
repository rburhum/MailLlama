"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Account


def get_db(session: Session = Depends(get_session)) -> Session:
    return session


def get_account(session: Session = Depends(get_db)) -> Account:
    """Single-user app: return the first (and only) account."""
    account = session.scalar(select(Account).order_by(Account.id).limit(1))
    if account is None:
        raise HTTPException(
            status_code=404,
            detail="No account connected. Visit /auth/gmail/start to authorize Gmail.",
        )
    return account


def maybe_account(session: Session = Depends(get_db)) -> Account | None:
    return session.scalar(select(Account).order_by(Account.id).limit(1))

"""Test fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime

import pytest
from cryptography.fernet import Fernet

# Configure test DB + a throwaway SECRET_KEY before importing the app.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("LLM_BASE_URL", "http://stub/v1")
os.environ.setdefault("LLM_API_KEY", "stub")

from mailllama import db as db_module  # noqa: E402
from mailllama.models import Account, Base  # noqa: E402


@pytest.fixture
def session() -> Iterator:
    # Fresh in-memory DB per test.
    Base.metadata.create_all(bind=db_module.engine)
    s = db_module.SessionLocal()
    try:
        yield s
    finally:
        s.close()
        Base.metadata.drop_all(bind=db_module.engine)


@pytest.fixture
def account(session) -> Account:
    acct = Account(provider="gmail_api", email="me@example.com")
    session.add(acct)
    session.commit()
    return acct

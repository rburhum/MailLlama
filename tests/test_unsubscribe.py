from __future__ import annotations

from mailllama.config import get_settings
from mailllama.services.sync import sync_account
from mailllama.services.unsubscribe import unsubscribe_message

from .fakes import FakeMailProvider, make_msg


def test_mailto_path_sends_email(session, account, monkeypatch):
    # Force dry_run OFF for this test (respecting .env defaults).
    monkeypatch.setattr(get_settings(), "dry_run", False, raising=False)

    provider = FakeMailProvider(
        email=account.email,
        messages=[make_msg(pid="a", list_unsub_mailto="unsub@newsletter.com")],
    )
    sync_account(session, account, provider)
    session.flush()
    from mailllama.models import Message
    msg = session.query(Message).one()

    result = unsubscribe_message(msg, provider, use_llm_fallback=False)
    assert result.method == "mailto"
    assert result.success is True
    assert provider.unsub_mailtos == ["unsub@newsletter.com"]


def test_no_unsub_method_returns_none(session, account):
    provider = FakeMailProvider(
        email=account.email,
        messages=[make_msg(pid="a", from_addr="plain@sender.com")],
    )
    sync_account(session, account, provider)
    session.flush()
    from mailllama.models import Message
    msg = session.query(Message).one()

    result = unsubscribe_message(msg, provider, use_llm_fallback=False)
    assert result.method == "none"
    assert result.success is False

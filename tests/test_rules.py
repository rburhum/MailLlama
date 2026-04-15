from __future__ import annotations

from mailllama.services.rules import add_rule, evaluate_message
from mailllama.services.sync import sync_account

from .fakes import FakeMailProvider, make_msg


def test_whitelist_overrides_blacklist(session, account):
    provider = FakeMailProvider(
        email=account.email,
        messages=[make_msg(pid="a", from_addr="boss@example.com")],
    )
    sync_account(session, account, provider)
    session.flush()
    msg = session.query(__import__("mailllama.models", fromlist=["Message"]).Message).one()

    add_rule(session, account_id=account.id, kind="blacklist", match_kind="domain",
             pattern="example.com", action="auto_trash")
    add_rule(session, account_id=account.id, kind="whitelist", match_kind="email",
             pattern="boss@example.com", action="ignore")
    session.flush()

    matches = evaluate_message(session, account.id, msg)
    assert len(matches) == 1
    assert matches[0].kind == "whitelist"


def test_domain_match(session, account):
    provider = FakeMailProvider(
        email=account.email,
        messages=[make_msg(pid="a", from_addr="news@subdomain.acme.com")],
    )
    sync_account(session, account, provider)
    msg = session.query(__import__("mailllama.models", fromlist=["Message"]).Message).one()
    add_rule(session, account_id=account.id, kind="blacklist", match_kind="domain",
             pattern="acme.com", action="auto_trash")
    session.flush()
    assert any(m.kind == "blacklist" for m in evaluate_message(session, account.id, msg))

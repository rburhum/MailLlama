from __future__ import annotations

from datetime import datetime

from mailllama.models import Message, Sender, Thread
from mailllama.services.sync import sync_account

from .fakes import FakeMailProvider, make_msg


def test_sync_creates_messages_threads_senders(session, account):
    provider = FakeMailProvider(
        email=account.email,
        messages=[
            make_msg(pid="a", from_addr="news@acme.com", thread="t1"),
            make_msg(pid="b", from_addr="news@acme.com", thread="t2"),
            make_msg(pid="c", from_addr="alice@friend.com", thread="t3"),
        ],
    )
    n = sync_account(session, account, provider)
    session.commit()
    assert n == 3
    assert session.query(Message).count() == 3
    assert session.query(Thread).count() == 3
    senders = {s.normalized_addr: s for s in session.query(Sender).all()}
    assert senders["news@acme.com"].message_count == 2
    assert senders["alice@friend.com"].message_count == 1


def test_sync_is_idempotent(session, account):
    msgs = [make_msg(pid="a"), make_msg(pid="b")]
    provider = FakeMailProvider(email=account.email, messages=msgs)
    sync_account(session, account, provider)
    sync_account(session, account, provider)
    session.commit()
    assert session.query(Message).count() == 2

from __future__ import annotations

from mailllama.models import Sender, Thread
from mailllama.services.interaction import compute_interactions
from mailllama.services.sync import sync_account

from .fakes import FakeMailProvider, make_msg


def test_reply_count_rolls_up_to_sender(session, account):
    msgs = [
        make_msg(pid="a", from_addr="alice@friend.com", thread="t1"),
        make_msg(pid="b", from_addr="alice@friend.com", thread="t2"),
        make_msg(pid="c", from_addr="bot@service.com", thread="t3"),
    ]
    provider = FakeMailProvider(
        email=account.email,
        messages=msgs,
        sent_in_thread={"t1": ["reply-a"]},  # user replied in thread 1
    )
    sync_account(session, account, provider)
    compute_interactions(session, account, provider)
    session.commit()

    threads = {t.thread_id: t for t in session.query(Thread).all()}
    assert threads["t1"].user_has_replied is True
    assert threads["t2"].user_has_replied is False
    assert threads["t3"].user_has_replied is False

    senders = {s.normalized_addr: s for s in session.query(Sender).all()}
    assert senders["alice@friend.com"].reply_count == 1
    assert senders["bot@service.com"].reply_count == 0

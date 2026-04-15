from __future__ import annotations

from mailllama.models import Sender
from mailllama.services.classify import classify_senders
from mailllama.services.sync import sync_account

from .fakes import FakeMailProvider, make_msg


class _StubLLM:
    """Stub LLM that labels any address starting with 'news' as 'newsletter'."""

    def __init__(self, *_, **__) -> None:
        self.model = "stub"

    def complete_json(self, system, user, **kwargs):
        import json
        import re

        classifications = []
        # Parse the user prompt JSON block (best-effort).
        m = re.search(r"\[(?:.|\n)*\]", user)
        if not m:
            return {"classifications": []}
        batch = json.loads(m.group(0))
        for item in batch:
            addr = item["from"]
            if addr.startswith("news"):
                label, conf = "newsletter", 0.9
            elif addr.startswith("alice"):
                label, conf = "personal", 0.95
            else:
                label, conf = "unknown", 0.5
            classifications.append(
                {
                    "sender_index": item["sender_index"],
                    "label": label,
                    "confidence": conf,
                    "reasoning": "stub",
                }
            )
        return {"classifications": classifications}


def test_classify_senders_writes_labels(session, account, monkeypatch):
    monkeypatch.setattr("mailllama.services.classify.LLMClient", _StubLLM)

    provider = FakeMailProvider(
        email=account.email,
        messages=[
            make_msg(pid="a", from_addr="news@acme.com"),
            make_msg(pid="b", from_addr="alice@friend.com"),
        ],
    )
    sync_account(session, account, provider)
    session.flush()
    classify_senders(session, account)
    session.commit()

    senders = {s.normalized_addr: s for s in session.query(Sender).all()}
    assert senders["news@acme.com"].latest_label == "newsletter"
    assert senders["alice@friend.com"].latest_label == "personal"

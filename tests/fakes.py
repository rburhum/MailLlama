"""In-memory fakes for tests."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from mailllama.providers.base import MailMessage, MailProvider


@dataclass
class FakeMailProvider(MailProvider):
    email: str = "me@example.com"
    messages: list[MailMessage] = field(default_factory=list)
    sent_in_thread: dict[str, list[str]] = field(default_factory=dict)
    trashed: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    moved: list[tuple[str, list[str]]] = field(default_factory=list)
    unsub_mailtos: list[str] = field(default_factory=list)
    body_map: dict[str, str] = field(default_factory=dict)

    def list_since(self, cursor, *, max_results=5000):
        return self.messages, "cursor-1"

    def fetch_body(self, provider_msg_id: str) -> str:
        return self.body_map.get(provider_msg_id, "")

    def sent_message_ids_in_thread(self, thread_id: str) -> list[str]:
        return list(self.sent_in_thread.get(thread_id, []))

    def batch_trash(self, provider_msg_ids: list[str]) -> None:
        self.trashed.extend(provider_msg_ids)

    def batch_archive(self, provider_msg_ids: list[str]) -> None:
        self.archived.extend(provider_msg_ids)

    def batch_modify_labels(self, provider_msg_ids, add=None, remove=None) -> None:
        for mid in provider_msg_ids:
            self.moved.append((mid, add or []))

    def send_mailto_unsubscribe(self, mailto: str, *, subject: str = "unsubscribe") -> None:
        self.unsub_mailtos.append(mailto)


def make_msg(
    *,
    pid: str,
    thread: str = "t1",
    from_addr: str = "news@example.com",
    subject: str = "Hello",
    date: datetime | None = None,
    size: int = 1024,
    list_unsub_http: str | None = None,
    list_unsub_mailto: str | None = None,
    one_click: bool = False,
) -> MailMessage:
    return MailMessage(
        provider_msg_id=pid,
        thread_id=thread,
        from_addr=from_addr,
        from_name=None,
        to_addrs=["me@example.com"],
        subject=subject,
        date=date or datetime(2026, 1, 1),
        size_bytes=size,
        list_unsub_http=list_unsub_http,
        list_unsub_mailto=list_unsub_mailto,
        list_unsub_one_click=one_click,
    )

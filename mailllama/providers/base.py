"""Mail provider abstract base class.

Both the Gmail API and IMAP backends implement this interface so the rest of
the app doesn't care which one is in use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class MailMessage:
    """Provider-agnostic message record."""

    provider_msg_id: str
    thread_id: str
    from_addr: str
    from_name: str | None
    to_addrs: list[str]
    subject: str | None
    date: datetime | None
    size_bytes: int
    snippet: str | None = None
    list_id: str | None = None
    list_unsub_http: str | None = None
    list_unsub_mailto: str | None = None
    list_unsub_one_click: bool = False
    has_attachments: bool = False
    is_read: bool = False
    labels: list[str] = field(default_factory=list)
    raw_headers: dict[str, Any] = field(default_factory=dict)


class MailProvider(ABC):
    """Minimum surface every provider must implement."""

    email: str

    @abstractmethod
    def list_since(
        self, cursor: str | None, *, max_results: int = 5000
    ) -> tuple[Iterable[MailMessage], str | None]:
        """Yield new/updated messages since the cursor.

        Returns (messages, new_cursor). Implementations should be lazy.
        """

    @abstractmethod
    def fetch_body(self, provider_msg_id: str) -> str:
        """Fetch the message body (HTML preferred, text fallback)."""

    @abstractmethod
    def sent_message_ids_in_thread(self, thread_id: str) -> list[str]:
        """Provider-msg-ids of messages in the thread that were sent by the user."""

    @abstractmethod
    def batch_trash(self, provider_msg_ids: list[str]) -> None:
        """Move messages to trash (reversible; 30-day retention in Gmail)."""

    @abstractmethod
    def batch_archive(self, provider_msg_ids: list[str]) -> None:
        """Remove INBOX label / move out of INBOX."""

    @abstractmethod
    def batch_modify_labels(
        self,
        provider_msg_ids: list[str],
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> None:
        """Add/remove labels (or for IMAP, move to folder named in ``add``)."""

    @abstractmethod
    def send_mailto_unsubscribe(self, mailto: str, *, subject: str = "unsubscribe") -> None:
        """Send an empty unsubscribe email per List-Unsubscribe mailto."""

"""IMAP provider.

Simpler (and less capable) than the Gmail API provider but works against any
mailbox. Maps MailProvider operations onto IMAP folders:

- trash   → move to configured TRASH_FOLDER
- archive → move to configured ARCHIVE_FOLDER (default ``[Gmail]/All Mail``)
"""

from __future__ import annotations

import email
import re
from collections.abc import Iterable
from datetime import datetime
from email.utils import parseaddr, parsedate_to_datetime

from imapclient import IMAPClient

from .base import MailMessage, MailProvider

TRASH_FOLDER = "[Gmail]/Trash"
ARCHIVE_FOLDER = "[Gmail]/All Mail"
SENT_FOLDER = "[Gmail]/Sent Mail"
INBOX = "INBOX"

_HTTPS_RE = re.compile(r"<(https?://[^>]+)>")
_MAILTO_RE = re.compile(r"<(mailto:[^>]+)>", re.IGNORECASE)


class IMAPProvider(MailProvider):
    def __init__(self, host: str, user: str, password: str, *, port: int = 993) -> None:
        self.email = user
        self._host = host
        self._port = port
        self._user = user
        self._password = password

    def _connect(self) -> IMAPClient:
        c = IMAPClient(self._host, port=self._port, ssl=True)
        c.login(self._user, self._password)
        return c

    def list_since(
        self, cursor: str | None, *, max_results: int = 5000
    ) -> tuple[Iterable[MailMessage], str | None]:
        with self._connect() as c:
            c.select_folder(INBOX, readonly=True)
            uids = c.search(["ALL"])
            uids = uids[-max_results:]
            messages: list[MailMessage] = []
            fetched = c.fetch(uids, ["RFC822.SIZE", "FLAGS", "BODY.PEEK[HEADER]", "INTERNALDATE"])
            for uid, data in fetched.items():
                msg = email.message_from_bytes(data[b"BODY[HEADER]"])
                from_raw = msg.get("From", "")
                from_name, from_addr = parseaddr(from_raw)
                date_hdr = msg.get("Date")
                try:
                    date = parsedate_to_datetime(date_hdr) if date_hdr else data.get(b"INTERNALDATE")
                    if isinstance(date, datetime) and date.tzinfo is not None:
                        date = date.astimezone().replace(tzinfo=None)
                except (TypeError, ValueError):
                    date = None
                list_unsub = msg.get("List-Unsubscribe", "")
                http_match = _HTTPS_RE.search(list_unsub)
                mailto_match = _MAILTO_RE.search(list_unsub)
                one_click = "One-Click" in msg.get("List-Unsubscribe-Post", "")
                flags = data.get(b"FLAGS") or ()
                seen = b"\\Seen" in flags
                messages.append(
                    MailMessage(
                        provider_msg_id=str(uid),
                        thread_id=msg.get("Message-ID", str(uid)).strip("<>"),
                        from_addr=from_addr.lower(),
                        from_name=from_name or None,
                        to_addrs=[
                            a.strip().lower()
                            for a in msg.get("To", "").split(",")
                            if a.strip()
                        ],
                        subject=msg.get("Subject"),
                        date=date if isinstance(date, datetime) else None,
                        size_bytes=int(data.get(b"RFC822.SIZE", 0) or 0),
                        snippet=None,
                        list_id=msg.get("List-Id"),
                        list_unsub_http=http_match.group(1) if http_match else None,
                        list_unsub_mailto=(
                            mailto_match.group(1).replace("mailto:", "")
                            if mailto_match
                            else None
                        ),
                        list_unsub_one_click=one_click and http_match is not None,
                        is_read=seen,
                        labels=[INBOX],
                        raw_headers={k: v for k, v in msg.items()},
                    )
                )
        # No real cursor; return max uid as an opaque marker.
        new_cursor = str(max(uids)) if uids else cursor
        return messages, new_cursor

    def fetch_body(self, provider_msg_id: str) -> str:
        uid = int(provider_msg_id)
        with self._connect() as c:
            c.select_folder(INBOX, readonly=True)
            data = c.fetch([uid], ["RFC822"])
            raw = data[uid][b"RFC822"]
            msg = email.message_from_bytes(raw)
        html = None
        text = None
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/html" and html is None:
                    html = part.get_payload(decode=True)
                elif ctype == "text/plain" and text is None:
                    text = part.get_payload(decode=True)
        else:
            text = msg.get_payload(decode=True)
        body_bytes = html or text or b""
        return body_bytes.decode("utf-8", errors="replace")

    def sent_message_ids_in_thread(self, thread_id: str) -> list[str]:
        # Heuristic for IMAP: search Sent folder by References / In-Reply-To header.
        with self._connect() as c:
            try:
                c.select_folder(SENT_FOLDER, readonly=True)
            except Exception:  # noqa: BLE001
                return []
            uids = c.search([
                "OR",
                "HEADER", "In-Reply-To", thread_id,
                "HEADER", "References", thread_id,
            ])
            return [str(u) for u in uids]

    def batch_trash(self, provider_msg_ids: list[str]) -> None:
        if not provider_msg_ids:
            return
        uids = [int(i) for i in provider_msg_ids]
        with self._connect() as c:
            c.select_folder(INBOX)
            c.move(uids, TRASH_FOLDER)

    def batch_archive(self, provider_msg_ids: list[str]) -> None:
        if not provider_msg_ids:
            return
        uids = [int(i) for i in provider_msg_ids]
        with self._connect() as c:
            c.select_folder(INBOX)
            c.move(uids, ARCHIVE_FOLDER)

    def batch_modify_labels(
        self,
        provider_msg_ids: list[str],
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> None:
        # IMAP has no label concept; interpret add[0] as a target folder to move into.
        if not provider_msg_ids or not add:
            return
        uids = [int(i) for i in provider_msg_ids]
        with self._connect() as c:
            c.select_folder(INBOX)
            c.move(uids, add[0])

    def send_mailto_unsubscribe(self, mailto: str, *, subject: str = "unsubscribe") -> None:
        # Sending email via IMAP isn't possible — use SMTP. We skip for v1 and
        # let the UI surface the mailto: link for the user to click.
        raise NotImplementedError(
            "Sending unsubscribe emails over IMAP requires SMTP configuration; "
            "use the Gmail API provider or the HTTP/browser unsubscribe path."
        )

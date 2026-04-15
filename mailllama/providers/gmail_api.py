"""Gmail API provider."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable
from datetime import datetime
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .base import MailMessage, MailProvider

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


_HTTPS_RE = re.compile(r"<(https?://[^>]+)>")
_MAILTO_RE = re.compile(r"<(mailto:[^>]+)>", re.IGNORECASE)


class GmailAPIProvider(MailProvider):
    def __init__(self, credentials_json: str, email: str | None = None) -> None:
        self._creds_json = credentials_json
        self._creds = Credentials.from_authorized_user_info(json.loads(credentials_json))
        self._svc = build("gmail", "v1", credentials=self._creds, cache_discovery=False)
        self.email = email or self._fetch_email()

    def _fetch_email(self) -> str:
        profile = self._svc.users().getProfile(userId="me").execute()
        return profile["emailAddress"]

    @property
    def credentials_json(self) -> str:
        # Refreshed creds may have a new access_token — callers should persist.
        return self._creds.to_json()

    # ----- reading -----

    def list_since(
        self, cursor: str | None, *, max_results: int = 5000
    ) -> tuple[Iterable[MailMessage], str | None]:
        # Simplified: ignore history API and just pull recent inbox messages.
        # (history API is optimal for incremental sync; we keep v1 pragmatic.)
        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < max_results:
            resp = (
                self._svc.users()
                .messages()
                .list(
                    userId="me",
                    labelIds=["INBOX"],
                    maxResults=min(500, max_results - len(ids)),
                    pageToken=page_token,
                )
                .execute()
            )
            ids.extend(m["id"] for m in resp.get("messages", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        new_cursor = self._svc.users().getProfile(userId="me").execute().get("historyId")
        msgs = (self._hydrate(mid) for mid in ids)
        return msgs, str(new_cursor) if new_cursor else cursor

    def _hydrate(self, msg_id: str) -> MailMessage:
        m = (
            self._svc.users()
            .messages()
            .get(userId="me", id=msg_id, format="metadata",
                 metadataHeaders=[
                     "From", "To", "Subject", "Date",
                     "List-Id", "List-Unsubscribe", "List-Unsubscribe-Post",
                 ])
            .execute()
        )
        headers = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}

        from_raw = headers.get("From", "")
        from_name, from_addr = parseaddr(from_raw)
        date_hdr = headers.get("Date")
        try:
            date = parsedate_to_datetime(date_hdr) if date_hdr else None
            if date and date.tzinfo is not None:
                date = date.astimezone().replace(tzinfo=None)
        except (TypeError, ValueError):
            date = None

        list_unsub = headers.get("List-Unsubscribe", "")
        http_match = _HTTPS_RE.search(list_unsub)
        mailto_match = _MAILTO_RE.search(list_unsub)
        one_click = "One-Click" in headers.get("List-Unsubscribe-Post", "")

        labels: list[str] = m.get("labelIds", [])
        return MailMessage(
            provider_msg_id=m["id"],
            thread_id=m["threadId"],
            from_addr=from_addr.lower(),
            from_name=from_name or None,
            to_addrs=[a.strip().lower() for a in headers.get("To", "").split(",") if a.strip()],
            subject=headers.get("Subject"),
            date=date,
            size_bytes=int(m.get("sizeEstimate", 0)),
            snippet=m.get("snippet"),
            list_id=headers.get("List-Id"),
            list_unsub_http=http_match.group(1) if http_match else None,
            list_unsub_mailto=mailto_match.group(1).replace("mailto:", "") if mailto_match else None,
            list_unsub_one_click=one_click and http_match is not None,
            is_read="UNREAD" not in labels,
            labels=labels,
            raw_headers=headers,
        )

    def fetch_body(self, provider_msg_id: str) -> str:
        m = (
            self._svc.users()
            .messages()
            .get(userId="me", id=provider_msg_id, format="full")
            .execute()
        )
        return _extract_body(m.get("payload", {}))

    def sent_message_ids_in_thread(self, thread_id: str) -> list[str]:
        t = self._svc.users().threads().get(userId="me", id=thread_id, format="minimal").execute()
        result: list[str] = []
        for m in t.get("messages", []):
            if "SENT" in (m.get("labelIds") or []):
                result.append(m["id"])
        return result

    # ----- writing -----

    def batch_trash(self, provider_msg_ids: list[str]) -> None:
        for mid in provider_msg_ids:
            self._svc.users().messages().trash(userId="me", id=mid).execute()

    def batch_archive(self, provider_msg_ids: list[str]) -> None:
        if not provider_msg_ids:
            return
        self._svc.users().messages().batchModify(
            userId="me",
            body={"ids": provider_msg_ids, "removeLabelIds": ["INBOX"]},
        ).execute()

    def batch_modify_labels(
        self,
        provider_msg_ids: list[str],
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> None:
        if not provider_msg_ids:
            return
        self._svc.users().messages().batchModify(
            userId="me",
            body={
                "ids": provider_msg_ids,
                "addLabelIds": add or [],
                "removeLabelIds": remove or [],
            },
        ).execute()

    def send_mailto_unsubscribe(self, mailto: str, *, subject: str = "unsubscribe") -> None:
        from email.mime.text import MIMEText

        msg = MIMEText("")
        msg["to"] = mailto
        msg["from"] = self.email
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        self._svc.users().messages().send(userId="me", body={"raw": raw}).execute()


def _extract_body(payload: dict[str, Any]) -> str:
    """Walk the MIME tree and return HTML if present, else text/plain."""
    html_parts: list[str] = []
    text_parts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            try:
                decoded = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                decoded = ""
            if mime == "text/html":
                html_parts.append(decoded)
            elif mime == "text/plain":
                text_parts.append(decoded)
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if html_parts:
        return "\n".join(html_parts)
    return "\n".join(text_parts)


def build_oauth_flow(client_id: str, client_secret: str, redirect_uri: str) -> Any:
    from google_auth_oauthlib.flow import Flow  # lazy

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    return Flow.from_client_config(client_config, scopes=GMAIL_SCOPES, redirect_uri=redirect_uri)

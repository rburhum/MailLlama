"""RFC 2369 / RFC 8058 unsubscribe handling."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from ..config import get_settings
from ..llm.client import LLMClient
from ..llm.prompts import UNSUB_EXTRACT_SYSTEM, build_unsub_extract_prompt
from ..models import Message
from ..providers.base import MailProvider


@dataclass
class UnsubResult:
    method: str  # one_click | http_link | mailto | body_link | none
    success: bool
    details: str | None = None


def unsubscribe_message(
    message: Message, provider: MailProvider, *, use_llm_fallback: bool = True
) -> UnsubResult:
    settings = get_settings()

    # 1. RFC 8058 one-click POST.
    if message.list_unsub_one_click and message.list_unsub_http:
        if settings.dry_run:
            return UnsubResult("one_click", True, "dry run")
        try:
            r = httpx.post(
                message.list_unsub_http,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data="List-Unsubscribe=One-Click",
                timeout=15,
                follow_redirects=True,
            )
            return UnsubResult(
                "one_click",
                200 <= r.status_code < 400,
                f"HTTP {r.status_code}",
            )
        except httpx.HTTPError as e:
            return UnsubResult("one_click", False, str(e))

    # 2. Plain https link — DON'T auto-GET (could trigger tracking or confirm).
    #    Surface the URL to the UI for the user to open manually.
    if message.list_unsub_http:
        return UnsubResult(
            "http_link", False, f"Open in browser: {message.list_unsub_http}"
        )

    # 3. mailto: unsubscribe — send via the provider.
    if message.list_unsub_mailto:
        if settings.dry_run:
            return UnsubResult("mailto", True, "dry run")
        try:
            provider.send_mailto_unsubscribe(message.list_unsub_mailto)
            return UnsubResult("mailto", True, f"sent to {message.list_unsub_mailto}")
        except NotImplementedError as e:
            return UnsubResult("mailto", False, str(e))
        except Exception as e:  # noqa: BLE001
            return UnsubResult("mailto", False, str(e))

    # 4. LLM fallback: scan the body for an unsubscribe link.
    if use_llm_fallback:
        body = provider.fetch_body(message.provider_msg_id)
        extracted = _extract_from_body(body)
        if extracted:
            return UnsubResult("body_link", False, f"Open in browser: {extracted}")

    return UnsubResult("none", False, "no unsubscribe method found")


def _extract_from_body(body: str) -> str | None:
    # Try a local HTML scan first (cheap, no LLM call).
    try:
        soup = BeautifulSoup(body, "html.parser")
        for a in soup.find_all("a", href=True):
            label = (a.get_text() or "").strip().lower()
            href = a["href"]
            if any(
                kw in label for kw in ("unsubscribe", "opt out", "manage preferences")
            ) and href.startswith("http"):
                return href
    except Exception:  # noqa: BLE001
        pass

    # LLM fallback — expensive, so only if heuristic found nothing.
    try:
        llm = LLMClient()
        resp = llm.complete_json(UNSUB_EXTRACT_SYSTEM, build_unsub_extract_prompt(body))
        url = (resp.get("url") or "").strip()
        return url or None
    except Exception:  # noqa: BLE001
        return None

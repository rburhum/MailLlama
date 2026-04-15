"""Prompt templates for classification and unsubscribe link extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

LABELS = [
    "spam",
    "subscription",
    "newsletter",
    "promo",
    "transactional",
    "personal",
    "unknown",
]

LABEL_GUIDE = """
Label definitions:
- spam: unsolicited bulk mail, phishing, scams.
- subscription: the user knowingly signed up (SaaS product updates, account
  notifications that are NOT transactional: e.g. "new features in Notion").
- newsletter: content-style bulk mail (blogs, digests, news).
- promo: marketing / sales / discounts / coupons.
- transactional: receipts, invoices, 2FA codes, order confirmations, password
  resets, shipping updates — things the user likely NEEDS.
- personal: from a human to the user personally, or work correspondence.
- unknown: genuinely ambiguous.
"""


SENDER_BATCH_SYSTEM = f"""You are a mail triage assistant. You classify EMAIL SENDERS
(not individual messages) for a user who wants to reach inbox zero.

{LABEL_GUIDE}

You output strict JSON: an object with a single key "classifications" which is
an array of objects with keys: sender_index (int), label (one of {LABELS}),
confidence (0..1 float), reasoning (short string, <= 140 chars).

Do not include any other keys. Do not include commentary outside the JSON.
"""


@dataclass
class SenderSample:
    """Aggregate info about a sender for batch classification."""

    normalized_addr: str
    display_name: str | None
    domain: str
    message_count: int
    reply_count: int
    sample_subjects: list[str]
    has_list_unsubscribe: bool
    list_id: str | None


def build_sender_batch_prompt(samples: list[SenderSample]) -> str:
    payload: list[dict[str, Any]] = []
    for i, s in enumerate(samples):
        payload.append(
            {
                "sender_index": i,
                "from": s.normalized_addr,
                "from_name": s.display_name,
                "domain": s.domain,
                "user_sent_reply_count": s.reply_count,
                "received_count": s.message_count,
                "list_unsubscribe_present": s.has_list_unsubscribe,
                "list_id": s.list_id,
                "recent_subjects": s.sample_subjects[:6],
            }
        )
    return (
        "Classify the following senders. Use recent subjects and whether the "
        "user has ever replied to help disambiguate personal vs. bulk mail. "
        "If the user has replied to this sender at least once and received "
        "count is low, favor 'personal' over bulk categories.\n\n"
        f"SENDERS:\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n"
    )


UNSUB_EXTRACT_SYSTEM = """You are a helper that finds unsubscribe links in email
HTML. Return strict JSON: {"url": "<https url or empty string>", "confidence": 0..1,
"reasoning": "<short>"}. Prefer https links with text like 'unsubscribe',
'manage preferences', 'opt out'. If no clear unsubscribe link, return an empty url.
"""


def build_unsub_extract_prompt(html_or_text: str) -> str:
    truncated = html_or_text[:8000]
    return f"EMAIL BODY:\n{truncated}\n"

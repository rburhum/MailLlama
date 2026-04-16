"""Chat service: LLM-driven inbox commands via tool/function calling.

The user types a natural language message. The LLM receives tool
definitions for inbox operations (search, trash, archive, unsubscribe,
add rules, etc.) and calls the appropriate ones. Results are fed back
so the LLM can summarize what happened.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..llm.client import LLMClient
from ..models import Account, Message, Sender, Thread
from ..providers.base import MailProvider
from ..services import actions as actions_svc
from ..services import rules as rules_svc
from ..services.sizes import top_messages_by_size, top_senders_by_size
from ..services.unsubscribe import unsubscribe_message

log = logging.getLogger(__name__)

# ---------- Tool definitions (OpenAI function-calling format) ----------

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_messages",
            "description": "Search messages by sender address, subject keyword, or both. Returns a list of matching messages with id, subject, from, date, and size.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_addr": {"type": "string", "description": "Filter by sender email (exact or substring match)"},
                    "subject_contains": {"type": "string", "description": "Filter by keyword in subject"},
                    "limit": {"type": "integer", "description": "Max results (default 25)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_senders",
            "description": "List senders sorted by message count, optionally filtered by classification label. Returns sender address, label, message count, reply count, and total size.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "enum": ["spam", "subscription", "newsletter", "promo", "transactional", "personal", "unknown"],
                        "description": "Filter by classification label",
                    },
                    "never_replied": {"type": "boolean", "description": "If true, only show senders the user has never replied to"},
                    "limit": {"type": "integer", "description": "Max results (default 25)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_inbox_stats",
            "description": "Get inbox overview: total messages, threads, senders, size, and a breakdown by classification label.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_large_messages",
            "description": "List the largest messages by size in bytes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of results (default 20)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_large_senders",
            "description": "List senders using the most inbox space.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of results (default 20)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trash_by_sender",
            "description": "Move all messages from a sender to trash (30-day reversible). Set only_unreplied=true to keep threads the user replied to.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sender_addr": {"type": "string", "description": "Sender email address"},
                    "only_unreplied": {"type": "boolean", "description": "Only trash threads the user never replied to (default false)"},
                },
                "required": ["sender_addr"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_by_sender",
            "description": "Archive all messages from a sender (remove from inbox, keep in All Mail).",
            "parameters": {
                "type": "object",
                "properties": {
                    "sender_addr": {"type": "string", "description": "Sender email address"},
                },
                "required": ["sender_addr"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unsubscribe_from_sender",
            "description": "Attempt to unsubscribe from a sender's mailing list using the most recent message's List-Unsubscribe header.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sender_addr": {"type": "string", "description": "Sender email address"},
                },
                "required": ["sender_addr"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_rule",
            "description": "Add a blacklist or whitelist rule. Whitelist rules always override blacklist rules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["blacklist", "whitelist"]},
                    "match_kind": {"type": "string", "enum": ["email", "domain"]},
                    "pattern": {"type": "string", "description": "Email address or domain to match"},
                    "action": {"type": "string", "enum": ["ignore", "auto_archive", "auto_trash"], "description": "Action to take (default: ignore)"},
                },
                "required": ["kind", "match_kind", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_rules",
            "description": "List all blacklist and whitelist rules.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


SYSTEM_PROMPT = """\
You are MailLlama, an AI assistant that helps the user manage their email inbox.
You can search messages, list senders, check inbox stats, trash or archive
messages, unsubscribe from mailing lists, and manage blacklist/whitelist rules.

When the user asks you to do something, use the available tools. You may call
multiple tools in sequence if needed.

Important safety rules:
- Trash is reversible (30 days in Gmail). Prefer trash over permanent deletion.
- When the user says "delete", use trash (it's reversible).
- For bulk destructive actions, briefly confirm what you're about to do and the
  count of affected messages before executing.
- Whitelist rules always override blacklist rules.
- If DRY_RUN mode is on, actions are logged but not executed.

Current inbox stats:
{stats_summary}
"""


# ---------- Tool execution ----------

def _execute_tool(
    name: str,
    args: dict[str, Any],
    session: Session,
    account: Account,
    provider: MailProvider,
) -> Any:
    """Dispatch a tool call to the appropriate service function."""
    aid = account.id

    if name == "search_messages":
        return _search_messages(session, aid, **args)
    if name == "list_senders":
        return _list_senders(session, aid, **args)
    if name == "get_inbox_stats":
        return _get_inbox_stats(session, aid)
    if name == "get_large_messages":
        return _get_large_messages(session, aid, **args)
    if name == "get_large_senders":
        return _get_large_senders(session, aid, **args)
    if name == "trash_by_sender":
        return _trash_by_sender(session, aid, provider, **args)
    if name == "archive_by_sender":
        return _archive_by_sender(session, aid, provider, **args)
    if name == "unsubscribe_from_sender":
        return _unsubscribe_from_sender(session, aid, provider, **args)
    if name == "add_rule":
        return _add_rule(session, aid, **args)
    if name == "list_rules":
        return _list_rules(session, aid)
    return {"error": f"Unknown tool: {name}"}


def _search_messages(
    session: Session, account_id: int, *,
    from_addr: str | None = None,
    subject_contains: str | None = None,
    limit: int = 25,
) -> list[dict]:
    q = select(Message).where(Message.account_id == account_id)
    if from_addr:
        q = q.where(Message.from_addr.contains(from_addr.lower()))
    if subject_contains:
        q = q.where(Message.subject.icontains(subject_contains))
    q = q.order_by(desc(Message.date)).limit(min(limit, 100))
    return [
        {
            "id": m.id,
            "from": m.from_addr,
            "subject": m.subject,
            "date": m.date.isoformat() if m.date else None,
            "size_bytes": m.size_bytes,
            "is_read": m.is_read,
        }
        for m in session.scalars(q).all()
    ]


def _list_senders(
    session: Session, account_id: int, *,
    label: str | None = None,
    never_replied: bool = False,
    limit: int = 25,
) -> list[dict]:
    q = select(Sender).where(Sender.account_id == account_id)
    if label:
        q = q.where(Sender.latest_label == label)
    if never_replied:
        q = q.where(Sender.reply_count == 0)
    q = q.order_by(desc(Sender.message_count)).limit(min(limit, 100))
    return [
        {
            "addr": s.normalized_addr,
            "domain": s.domain,
            "label": s.latest_label,
            "message_count": s.message_count,
            "reply_count": s.reply_count,
            "total_size_bytes": s.total_size_bytes,
        }
        for s in session.scalars(q).all()
    ]


def _get_inbox_stats(session: Session, account_id: int) -> dict:
    messages = session.scalar(
        select(func.count(Message.id)).where(Message.account_id == account_id)
    ) or 0
    threads = session.scalar(
        select(func.count(Thread.id)).where(Thread.account_id == account_id)
    ) or 0
    senders = session.scalar(
        select(func.count(Sender.id)).where(Sender.account_id == account_id)
    ) or 0
    total_size = session.scalar(
        select(func.coalesce(func.sum(Message.size_bytes), 0)).where(
            Message.account_id == account_id
        )
    ) or 0
    # Label breakdown.
    label_counts = dict(
        session.execute(
            select(Sender.latest_label, func.count(Sender.id))
            .where(Sender.account_id == account_id, Sender.latest_label.isnot(None))
            .group_by(Sender.latest_label)
        ).all()
    )
    return {
        "messages": messages,
        "threads": threads,
        "senders": senders,
        "total_size_bytes": total_size,
        "senders_by_label": label_counts,
    }


def _get_large_messages(session: Session, account_id: int, *, limit: int = 20) -> list[dict]:
    rows = top_messages_by_size(session, account_id, limit=min(limit, 100))
    return [
        {"id": r.id, "from": r.from_addr, "subject": r.subject, "size_bytes": r.size_bytes}
        for r in rows
    ]


def _get_large_senders(session: Session, account_id: int, *, limit: int = 20) -> list[dict]:
    rows = top_senders_by_size(session, account_id, limit=min(limit, 100))
    return [
        {"addr": r.addr, "domain": r.domain, "message_count": r.message_count, "total_size_bytes": r.total_size_bytes}
        for r in rows
    ]


def _trash_by_sender(
    session: Session, account_id: int, provider: MailProvider, *,
    sender_addr: str, only_unreplied: bool = False,
) -> dict:
    n = actions_svc.batch_trash_by_sender(
        session, account_id, sender_addr, provider, only_unreplied=only_unreplied
    )
    session.commit()
    return {"trashed": n, "sender": sender_addr, "only_unreplied": only_unreplied}


def _archive_by_sender(
    session: Session, account_id: int, provider: MailProvider, *,
    sender_addr: str,
) -> dict:
    msg_ids = [
        m.id
        for m in session.scalars(
            select(Message).where(
                Message.account_id == account_id,
                Message.from_addr == sender_addr.lower(),
            )
        ).all()
    ]
    if not msg_ids:
        return {"archived": 0, "sender": sender_addr}
    n = actions_svc.batch_archive(session, account_id, msg_ids, provider)
    session.commit()
    return {"archived": n, "sender": sender_addr}


def _unsubscribe_from_sender(
    session: Session, account_id: int, provider: MailProvider, *,
    sender_addr: str,
) -> dict:
    # Find the most recent message from this sender with unsubscribe info.
    msg = session.scalar(
        select(Message)
        .where(
            Message.account_id == account_id,
            Message.from_addr == sender_addr.lower(),
        )
        .order_by(desc(Message.date))
        .limit(1)
    )
    if msg is None:
        return {"success": False, "detail": f"No messages found from {sender_addr}"}
    result = unsubscribe_message(msg, provider)
    return {"success": result.success, "method": result.method, "detail": result.details}


def _add_rule(
    session: Session, account_id: int, *,
    kind: str, match_kind: str, pattern: str, action: str = "ignore",
) -> dict:
    rule = rules_svc.add_rule(
        session, account_id=account_id,
        kind=kind, match_kind=match_kind, pattern=pattern, action=action,
    )
    session.commit()
    return {"rule_id": rule.id, "kind": kind, "match_kind": match_kind, "pattern": pattern, "action": action}


def _list_rules(session: Session, account_id: int) -> list[dict]:
    return [
        {"id": r.id, "kind": r.kind, "match_kind": r.match_kind, "pattern": r.pattern, "action": r.action, "enabled": r.enabled}
        for r in rules_svc.list_rules(session, account_id)
    ]


# ---------- Chat orchestration ----------

def _build_stats_summary(session: Session, account_id: int) -> str:
    stats = _get_inbox_stats(session, account_id)
    lines = [
        f"- {stats['messages']} messages, {stats['threads']} threads, {stats['senders']} senders",
        f"- Total size: {stats['total_size_bytes']:,} bytes",
    ]
    if stats["senders_by_label"]:
        breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(stats["senders_by_label"].items()))
        lines.append(f"- Senders by label: {breakdown}")
    settings = get_settings()
    if settings.dry_run:
        lines.append("- DRY_RUN mode is ON: destructive actions will be logged but not executed.")
    return "\n".join(lines)


def process_message(
    session: Session,
    account: Account,
    provider: MailProvider,
    user_message: str,
    history: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Process a user chat message and return (assistant_reply, updated_history).

    Uses tool/function calling when the LLM supports it. Falls back to a
    plain text response if tool calling isn't available.
    """
    llm = LLMClient()
    stats_summary = _build_stats_summary(session, account.id)
    system = SYSTEM_PROMPT.format(stats_summary=stats_summary)

    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    # First LLM call — may return tool_calls or a plain text response.
    try:
        resp = llm.chat(messages, tools=TOOLS)
    except Exception:
        # Model may not support tools — fall back to plain chat.
        log.warning("Tool calling failed, falling back to plain chat", exc_info=True)
        resp = llm.chat(messages)

    choice = resp.choices[0]
    assistant_msg = choice.message

    # If the LLM made tool calls, execute them and feed results back.
    if assistant_msg.tool_calls:
        # Append the assistant's tool-call message to history.
        messages.append({
            "role": "assistant",
            "content": assistant_msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in assistant_msg.tool_calls
            ],
        })

        for tc in assistant_msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
            except json.JSONDecodeError:
                args = {}
            log.info("Chat tool call: %s(%s)", tc.function.name, args)
            result = _execute_tool(tc.function.name, args, session, account, provider)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, default=str),
            })

        # Second LLM call — summarize results for the user.
        resp2 = llm.chat(messages)
        reply = resp2.choices[0].message.content or "(no response)"
    else:
        reply = assistant_msg.content or "(no response)"

    # Build clean history for the client (system prompt excluded).
    out_history = [m for m in messages[1:] if m["role"] != "system"]
    out_history.append({"role": "assistant", "content": reply})

    return reply, out_history

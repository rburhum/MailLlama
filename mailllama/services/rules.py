"""Blacklist / whitelist rules.

Whitelist wins over blacklist. Rules are advisory — they tag messages but do
not auto-execute destructive actions in v1 unless the user explicitly opts
in per-rule (``action == 'auto_archive' | 'auto_trash'``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Message, Rule


@dataclass
class RuleMatch:
    rule_id: int
    kind: str           # blacklist | whitelist
    action: str
    pattern: str


def evaluate_message(session: Session, account_id: int, message: Message) -> list[RuleMatch]:
    rules = list(
        session.scalars(
            select(Rule).where(Rule.account_id == account_id, Rule.enabled.is_(True))
        ).all()
    )
    matches = [RuleMatch(r.id, r.kind, r.action, r.pattern) for r in rules if _matches(r, message)]
    # Whitelist precedence.
    if any(m.kind == "whitelist" for m in matches):
        return [m for m in matches if m.kind == "whitelist"]
    return matches


def _matches(rule: Rule, message: Message) -> bool:
    pat = rule.pattern.lower()
    if rule.match_kind == "email":
        return message.from_addr.lower() == pat
    if rule.match_kind == "domain":
        return message.from_addr.lower().endswith("@" + pat) or message.from_addr.lower().endswith(
            "." + pat
        )
    if rule.match_kind == "header":
        # pattern is "Header-Name:substring"
        if ":" not in rule.pattern:
            return False
        name, substr = rule.pattern.split(":", 1)
        value = message.raw_headers.get(name.strip())
        return bool(value) and substr.strip().lower() in str(value).lower()
    if rule.match_kind == "regex":
        try:
            return re.search(rule.pattern, f"{message.from_addr} {message.subject or ''}") is not None
        except re.error:
            return False
    return False


def add_rule(
    session: Session,
    *,
    account_id: int,
    kind: str,
    match_kind: str,
    pattern: str,
    action: str = "ignore",
    notes: str | None = None,
) -> Rule:
    if kind not in ("blacklist", "whitelist"):
        raise ValueError("kind must be 'blacklist' or 'whitelist'")
    if match_kind not in ("email", "domain", "header", "regex"):
        raise ValueError("invalid match_kind")
    if action not in ("ignore", "auto_archive", "auto_trash"):
        raise ValueError("invalid action")
    rule = Rule(
        account_id=account_id,
        kind=kind,
        match_kind=match_kind,
        pattern=pattern,
        action=action,
        notes=notes,
    )
    session.add(rule)
    session.flush()
    return rule


def list_rules(session: Session, account_id: int) -> list[Rule]:
    return list(
        session.scalars(
            select(Rule).where(Rule.account_id == account_id).order_by(Rule.id.desc())
        ).all()
    )


def delete_rule(session: Session, account_id: int, rule_id: int) -> bool:
    r = session.get(Rule, rule_id)
    if r is None or r.account_id != account_id:
        return False
    session.delete(r)
    return True

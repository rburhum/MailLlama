"""Sender-level LLM classification (batched + cached)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

from ..cache import get_cache
from ..config import get_settings
from ..llm.client import LLMClient
from ..llm.prompts import (
    SENDER_BATCH_SYSTEM,
    SenderSample,
    build_sender_batch_prompt,
)
from ..models import Account, Message, Sender
from ..tasks.runner import TaskHandle

CLASSIFY_CACHE_TTL = 60 * 60 * 24 * 14  # 14 days


def _sample_subjects(session: Session, account_id: int, addr: str, limit: int = 6) -> list[str]:
    rows = session.scalars(
        select(Message.subject)
        .where(Message.account_id == account_id, Message.from_addr == addr)
        .order_by(Message.date.desc())
        .limit(limit)
    ).all()
    return [s for s in rows if s]


def _cache_key(sample: SenderSample, model: str) -> str:
    payload = json.dumps(
        {
            "addr": sample.normalized_addr,
            "subjects": sample.sample_subjects,
            "count": sample.message_count,
            "reply_count": sample.reply_count,
            "list": sample.has_list_unsubscribe,
            "model": model,
        },
        sort_keys=True,
    )
    return "classify:sender:" + hashlib.sha1(payload.encode()).hexdigest()


def classify_senders(
    session: Session,
    account: Account,
    *,
    handle: TaskHandle | None = None,
    batch_size: int = 20,
    only_unclassified: bool = True,
) -> int:
    settings = get_settings()
    model = settings.classify_model
    llm = LLMClient(model=model)
    cache = get_cache()

    q = select(Sender).where(Sender.account_id == account.id)
    if only_unclassified:
        q = q.where(Sender.latest_label.is_(None))
    senders: list[Sender] = list(session.scalars(q).all())

    if handle:
        handle.update(session=session, total=len(senders), message=f"Classifying {len(senders)} senders")
    if not senders:
        return 0

    done = 0
    for start in range(0, len(senders), batch_size):
        chunk = senders[start : start + batch_size]
        samples: list[SenderSample] = []
        for s in chunk:
            subjects = _sample_subjects(session, account.id, s.normalized_addr)
            has_list = bool(
                session.scalar(
                    select(Message.id)
                    .where(
                        Message.account_id == account.id,
                        Message.from_addr == s.normalized_addr,
                        Message.list_unsub_http.isnot(None),
                    )
                    .limit(1)
                )
            )
            list_id = session.scalar(
                select(Message.list_id)
                .where(
                    Message.account_id == account.id,
                    Message.from_addr == s.normalized_addr,
                    Message.list_id.isnot(None),
                )
                .limit(1)
            )
            samples.append(
                SenderSample(
                    normalized_addr=s.normalized_addr,
                    display_name=None,
                    domain=s.domain,
                    message_count=s.message_count,
                    reply_count=s.reply_count,
                    sample_subjects=subjects,
                    has_list_unsubscribe=has_list,
                    list_id=list_id,
                )
            )

        # Cache lookup per sender.
        todo_indices: list[int] = []
        for i, sample in enumerate(samples):
            cached = cache.get(_cache_key(sample, model))
            if cached:
                payload = json.loads(cached)
                _apply_classification(chunk[i], payload, model)
                done += 1
                if handle:
                    handle.update(session=session, progress=done, message=f"Classified {done}")
            else:
                todo_indices.append(i)

        if todo_indices:
            batch_samples = [samples[i] for i in todo_indices]
            prompt = build_sender_batch_prompt(batch_samples)
            response = llm.complete_json(SENDER_BATCH_SYSTEM, prompt, model=model)
            by_idx = _parse_classifications(response)
            for local_idx, global_i in enumerate(todo_indices):
                c = by_idx.get(local_idx) or {
                    "label": "unknown",
                    "confidence": 0.0,
                    "reasoning": "no response",
                }
                payload = {
                    "label": c.get("label", "unknown"),
                    "confidence": float(c.get("confidence", 0.0) or 0.0),
                    "reasoning": c.get("reasoning", ""),
                }
                _apply_classification(chunk[global_i], payload, model)
                cache.set(
                    _cache_key(samples[global_i], model),
                    json.dumps(payload),
                    ttl_seconds=CLASSIFY_CACHE_TTL,
                )
                done += 1
                if handle:
                    handle.update(session=session, progress=done, message=f"Classified {done}")
        session.commit()  # release write lock between batches
    return done


def _parse_classifications(response: dict) -> dict[int, dict]:
    """Tolerantly extract a sender_index → classification map from the LLM.

    The prompt asks for::

        {"classifications": [{"sender_index": 0, "label": ..., ...}, ...]}

    but different models sometimes return variations like:
      - a bare list at the top level: ``[{...}, {...}]``
      - a dict keyed by index: ``{"0": {...}, "1": {...}}``
      - items that are lists/tuples instead of dicts
      - items missing ``sender_index`` (assume positional)

    Anything we can't interpret is logged and skipped; the caller treats
    missing indices as "unknown" so a malformed batch doesn't kill the
    whole run.
    """
    if isinstance(response, list):
        items = response
    elif isinstance(response, dict):
        items = response.get("classifications") or response.get("results") or []
        if isinstance(items, dict):
            # Convert dict-keyed-by-index to a list of items.
            items = [{**v, "sender_index": int(k)} for k, v in items.items() if isinstance(v, dict)]
    else:
        items = []

    by_idx: dict[int, dict] = {}
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            log.warning("classify: skipping non-dict classification item: %r", item)
            continue
        idx = item.get("sender_index", position)
        try:
            idx_int = int(idx)
        except (TypeError, ValueError):
            log.warning("classify: bad sender_index %r, using position %d", idx, position)
            idx_int = position
        by_idx[idx_int] = item
    if not by_idx and items:
        log.warning("classify: could not parse LLM response; raw=%r", response)
    return by_idx


def _apply_classification(sender: Sender, payload: dict, model: str) -> None:
    sender.latest_label = payload.get("label", "unknown")
    sender.latest_confidence = float(payload.get("confidence", 0.0))
    sender.latest_reasoning = payload.get("reasoning")
    sender.classified_at = datetime.utcnow()
    # Model stamp isn't stored on sender; lives in per-message Classification rows
    # if we later choose to persist them. Keeping this pragmatic for v1.
    _ = model

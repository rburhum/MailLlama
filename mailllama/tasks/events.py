"""Simple in-memory pub/sub for task progress → SSE.

TaskHandle.update() calls ``notify(task_id, data)`` which pushes to all
queues subscribed to that task. The SSE endpoint in web/routes/tasks.py
holds one queue per connected client.

Thread-safe: notify() is called from worker threads, subscribe/unsubscribe
from the async event loop.
"""

from __future__ import annotations

import queue
import threading
from typing import Any


_lock = threading.Lock()
_subscribers: dict[int, list[queue.Queue[dict[str, Any]]]] = {}


def subscribe(task_id: int) -> queue.Queue[dict[str, Any]]:
    """Create and register a queue that receives events for ``task_id``."""
    q: queue.Queue[dict[str, Any]] = queue.Queue()
    with _lock:
        _subscribers.setdefault(task_id, []).append(q)
    return q


def unsubscribe(task_id: int, q: queue.Queue[dict[str, Any]]) -> None:
    with _lock:
        subs = _subscribers.get(task_id)
        if subs:
            _subscribers[task_id] = [x for x in subs if x is not q]
            if not _subscribers[task_id]:
                del _subscribers[task_id]


def notify(task_id: int, data: dict[str, Any]) -> None:
    """Push ``data`` to all queues subscribed to ``task_id``."""
    with _lock:
        subs = list(_subscribers.get(task_id, []))
    for q in subs:
        try:
            q.put_nowait(data)
        except queue.Full:
            pass  # drop if consumer is too slow

"""Small key/value cache abstraction.

Default backend is an in-memory TTL dict (no DB writes, no locking).
If ``REDIS_URL`` is set in settings, use Redis instead.
``SqliteCache`` is still available but no longer the default — it caused
``database is locked`` errors when tasks ran concurrently.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod

from .config import get_settings


class Cache(ABC):
    @abstractmethod
    def get(self, key: str) -> str | None: ...

    @abstractmethod
    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...


class MemoryCache(Cache):
    """Thread-safe in-memory cache with optional per-key TTL.

    Data is lost on restart, which is fine for sender classification
    caching — the LLM just re-classifies (idempotent). Use Redis for
    persistence across restarts.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}  # key → (value, expires_at)
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires = entry
            if expires is not None and time.monotonic() > expires:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        expires = time.monotonic() + ttl_seconds if ttl_seconds else None
        with self._lock:
            self._store[key] = (value, expires)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)


class RedisCache(Cache):
    def __init__(self, url: str) -> None:
        import redis  # type: ignore[import-not-found]

        self._r = redis.Redis.from_url(url, decode_responses=True)

    def get(self, key: str) -> str | None:
        return self._r.get(key)

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        if ttl_seconds:
            self._r.setex(key, ttl_seconds, value)
        else:
            self._r.set(key, value)

    def delete(self, key: str) -> None:
        self._r.delete(key)


_cache_singleton: Cache | None = None


def get_cache() -> Cache:
    global _cache_singleton
    if _cache_singleton is not None:
        return _cache_singleton
    settings = get_settings()
    if settings.redis_url:
        _cache_singleton = RedisCache(settings.redis_url)
    else:
        _cache_singleton = MemoryCache()
    return _cache_singleton

"""Small key/value cache abstraction.

Default backend is the sqlite-backed ``kv_cache`` table. If ``REDIS_URL`` is
set in settings, use Redis instead (requires ``pip install .[redis]``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from .config import get_settings
from .db import session_scope
from .models import KVCache


class Cache(ABC):
    @abstractmethod
    def get(self, key: str) -> str | None: ...

    @abstractmethod
    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...


class SqliteCache(Cache):
    def get(self, key: str) -> str | None:
        with session_scope() as session:
            row = session.get(KVCache, key)
            if row is None:
                return None
            if row.expires_at and row.expires_at < datetime.utcnow():
                session.delete(row)
                return None
            return row.value

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        expires = (
            datetime.utcnow() + timedelta(seconds=ttl_seconds) if ttl_seconds else None
        )
        with session_scope() as session:
            row = session.get(KVCache, key)
            if row is None:
                session.add(KVCache(key=key, value=value, expires_at=expires))
            else:
                row.value = value
                row.expires_at = expires

    def delete(self, key: str) -> None:
        with session_scope() as session:
            session.execute(delete(KVCache).where(KVCache.key == key))


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
        _cache_singleton = SqliteCache()
    return _cache_singleton

"""SQLAlchemy engine + session factory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_settings


def _make_engine() -> Engine:
    url = get_settings().database_url
    connect_args: dict[str, object] = {}
    kwargs: dict[str, object] = {"future": True}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        # Share a single connection across threads for in-memory DBs, so the
        # whole app sees the same data (important for tests).
        if ":memory:" in url or url.endswith(":memory:"):
            kwargs["poolclass"] = StaticPool
    eng = create_engine(url, connect_args=connect_args, **kwargs)

    # Enable WAL journal mode for SQLite. WAL allows concurrent readers
    # alongside a writer, which prevents "database is locked" when the web
    # app polls task progress while a sync/classify task is writing.
    if url.startswith("sqlite") and ":memory:" not in url:
        from sqlalchemy import event

        @event.listens_for(eng, "connect")
        def _set_sqlite_wal(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return eng


engine: Engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

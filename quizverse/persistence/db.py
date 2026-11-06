"""Database configuration and session management; schema changes are migration-only."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///./quizverse.db"


def database_url_from_env() -> str:
    """Return the configured URL without logging or otherwise exposing credentials."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def create_database_engine(url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine for SQLite or a configured PostgreSQL URL."""
    database_url = url or database_url_from_env()
    parsed = make_url(database_url)
    if parsed.get_backend_name() not in {"sqlite", "postgresql"}:
        raise ValueError("DATABASE_URL must use SQLite or PostgreSQL.")

    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if parsed.get_backend_name() == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(database_url, **kwargs)

    if parsed.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_sqlite_constraints(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


class Database:
    """Owns connections and transactions, but deliberately never creates schema."""

    def __init__(self, url: str | None = None) -> None:
        self.engine = create_database_engine(url)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Yield a session; callers choose and own transaction boundaries."""
        with self.sessions() as session:
            yield session

    def close(self) -> None:
        self.engine.dispose()

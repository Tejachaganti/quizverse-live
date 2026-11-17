"""Database configuration and session management; schema changes are migration-only."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///./quizverse.db"


def database_url_from_env() -> str:
    """Return the configured URL without logging or otherwise exposing credentials."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        # Check for discrete PostgreSQL environment variables if DATABASE_URL is not set
        pg_user = os.environ.get("POSTGRES_USER")
        pg_host = os.environ.get("POSTGRES_HOST")
        pg_db = os.environ.get("POSTGRES_DB")
        if pg_user and pg_host and pg_db:
            pg_pass = os.environ.get("POSTGRES_PASSWORD", "")
            pg_port = os.environ.get("POSTGRES_PORT", "5432")
            auth = f"{pg_user}:{pg_pass}" if pg_pass else pg_user
            url = f"postgresql+psycopg://{auth}@{pg_host}:{pg_port}/{pg_db}"
        else:
            url = DEFAULT_DATABASE_URL

    env_mode = (os.environ.get("ENVIRONMENT") or os.environ.get("ENV") or "").strip().lower()
    if env_mode == "production":
        parsed = make_url(url)
        if parsed.get_backend_name() != "postgresql":
            raise RuntimeError(
                "Production environment requires PostgreSQL. "
                "SQLite is not permitted when ENVIRONMENT=production. "
                "Please configure a valid PostgreSQL DATABASE_URL."
            )
    return url


def create_database_engine(url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine for SQLite or a configured PostgreSQL URL."""
    database_url = url or database_url_from_env()
    parsed = make_url(database_url)
    backend = parsed.get_backend_name()
    if backend not in {"sqlite", "postgresql"}:
        raise ValueError("DATABASE_URL must use SQLite or PostgreSQL.")

    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if backend == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    elif backend == "postgresql":
        kwargs["pool_size"] = int(os.environ.get("DB_POOL_SIZE", "10"))
        kwargs["max_overflow"] = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
        kwargs["pool_recycle"] = int(os.environ.get("DB_POOL_RECYCLE", "1800"))
        kwargs["pool_timeout"] = int(os.environ.get("DB_POOL_TIMEOUT", "30"))

    engine = create_engine(database_url, **kwargs)

    if backend == "sqlite":
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
        """Yield a session; callers choose and own transaction boundaries. Rolls back on error."""
        session = self.sessions()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self, timeout: float = 5.0) -> bool:
        """Lightweight connectivity probe for readiness checks."""
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def check_readiness(self) -> dict[str, object]:
        """Detailed status dictionary for health/readiness endpoints."""
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return {
                "connected": True,
                "backend": self.engine.dialect.name,
                "driver": getattr(self.engine.dialect, "driver", "unknown"),
            }
        except Exception as exc:
            return {
                "connected": False,
                "backend": self.engine.dialect.name,
                "error": type(exc).__name__,
            }

    def close(self) -> None:
        self.engine.dispose()

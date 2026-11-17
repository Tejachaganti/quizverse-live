"""Tests for Stage 12 Production Readiness: health, readiness probe, CORS, security headers, and fail-fast configuration."""

import os
import pytest
from fastapi.testclient import TestClient

from quizverse.persistence.db import Database, create_database_engine, database_url_from_env
from quizverse.realtime import create_app


def test_health_endpoint():
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["service"] == "quizverse-live"
        assert "pending_match_history" in data
        assert "retrying_match_history" in data


def test_readiness_probe_success():
    db = Database("sqlite:///:memory:")
    app = create_app(database=db)
    with TestClient(app) as client:
        resp = client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True
        assert data["service"] == "quizverse-live"
        assert data["database"]["connected"] is True
        assert data["database"]["backend"] == "sqlite"


def test_readiness_probe_database_failure(monkeypatch):
    db = Database("sqlite:///:memory:")
    app = create_app(database=db)
    
    # Simulate database connectivity failure
    monkeypatch.setattr(db, "check_readiness", lambda: {"connected": False, "backend": "sqlite", "error": "ConnectionRefused"})

    with TestClient(app) as client:
        resp = client.get("/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["ready"] is False
        assert data["database"]["connected"] is False


def test_security_headers_present():
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        headers = resp.headers
        assert "default-src 'self'" in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in headers["permissions-policy"]


def test_cors_middleware_headers():
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/health", headers={"Origin": "http://localhost:8000"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:8000"
        assert resp.headers.get("access-control-allow-credentials") == "true"


def test_production_environment_rejects_sqlite(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./quizverse.db")
    with pytest.raises(RuntimeError, match="Production environment requires PostgreSQL"):
        database_url_from_env()


def test_production_environment_accepts_postgresql(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@host:5432/db")
    url = database_url_from_env()
    assert "postgresql+psycopg://" in url


def test_postgresql_connection_pool_configuration(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "12")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "24")
    monkeypatch.setenv("DB_POOL_RECYCLE", "900")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "40")
    engine = create_database_engine("postgresql+psycopg://user:pass@host:5432/db")
    assert engine.pool.size() == 12
    assert engine.pool._max_overflow == 24
    assert engine.pool._recycle == 900
    assert engine.pool._timeout == 40

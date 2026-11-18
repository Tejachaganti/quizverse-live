"""Tests for WebSocket origin validation, normalization, proxy IP extraction, and rate limiting."""

from __future__ import annotations

import asyncio
import logging
import os
from unittest.mock import AsyncMock, patch

import pytest
from starlette.datastructures import Headers
from starlette.websockets import WebSocket

from quizverse.presence_api import (
    _allowed_origins as presence_allowed_origins,
    _client_peer as presence_client_peer,
    _valid_origin as presence_valid_origin,
    normalize_origin as presence_normalize_origin,
)
from quizverse.realtime import (
    ConnectionHub,
    _allowed_origins,
    _client_peer,
    _valid_origin,
    normalize_origin,
)


class DummyWebSocket:
    """Mock WebSocket for unit testing origin and peer extraction."""

    def __init__(
        self,
        origin: str | None = None,
        client_host: str | None = "10.0.0.1",
        forwarded_for: str | None = None,
    ) -> None:
        raw_headers: dict[str, str] = {}
        if origin is not None:
            raw_headers["origin"] = origin
        if forwarded_for is not None:
            raw_headers["x-forwarded-for"] = forwarded_for
        self.headers = Headers(raw_headers)
        self.client = type("Client", (), {"host": client_host})() if client_host else None
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.accepted = False

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason

    async def accept(self) -> None:
        self.accepted = True


# ==============================================================================
# 1. Origin Normalization Tests
# ==============================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://quizverse-live-gamma.vercel.app", "https://quizverse-live-gamma.vercel.app"),
        ("https://quizverse-live-gamma.vercel.app/", "https://quizverse-live-gamma.vercel.app"),
        ('"https://quizverse-live-gamma.vercel.app"', "https://quizverse-live-gamma.vercel.app"),
        ("'https://quizverse-live-gamma.vercel.app/'", "https://quizverse-live-gamma.vercel.app"),
        ("  https://QUIZVERSE-LIVE-GAMMA.vercel.app/  ", "https://quizverse-live-gamma.vercel.app"),
        ("http://localhost:8000/", "http://localhost:8000"),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_origin_handles_formatting(raw: str | None, expected: str):
    assert normalize_origin(raw) == expected
    assert presence_normalize_origin(raw) == expected


def test_allowed_origins_normalizes_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        ' "https://quizverse-live-gamma.vercel.app/", \'http://localhost:8000/\' , https://CUSTOM.example ',
    )
    expected = {
        "https://quizverse-live-gamma.vercel.app",
        "http://localhost:8000",
        "https://custom.example",
    }
    assert _allowed_origins() == expected
    assert presence_allowed_origins() == expected


# ==============================================================================
# 2. _valid_origin Tests (realtime.py and presence_api.py)
# ==============================================================================


def test_valid_origin_accepts_production_origin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://quizverse-live-gamma.vercel.app")
    ws = DummyWebSocket(origin="https://quizverse-live-gamma.vercel.app")
    assert _valid_origin(ws) is True
    assert presence_valid_origin(ws) is True


def test_valid_origin_accepts_trailing_slash_in_env(monkeypatch: pytest.MonkeyPatch):
    # User entered trailing slash in Render dashboard
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://quizverse-live-gamma.vercel.app/")
    # Browser sends clean origin without trailing slash
    ws = DummyWebSocket(origin="https://quizverse-live-gamma.vercel.app")
    assert _valid_origin(ws) is True
    assert presence_valid_origin(ws) is True


def test_valid_origin_accepts_quotes_and_case_in_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", '"https://QuizVerse-Live-Gamma.vercel.app/"')
    ws = DummyWebSocket(origin="https://quizverse-live-gamma.vercel.app")
    assert _valid_origin(ws) is True
    assert presence_valid_origin(ws) is True


def test_valid_origin_rejects_disallowed_origin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://quizverse-live-gamma.vercel.app")
    ws = DummyWebSocket(origin="https://malicious.example.com")
    assert _valid_origin(ws) is False
    assert presence_valid_origin(ws) is False


def test_valid_origin_permits_missing_origin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://quizverse-live-gamma.vercel.app")
    ws = DummyWebSocket(origin=None)
    assert _valid_origin(ws) is True
    assert presence_valid_origin(ws) is True


# ==============================================================================
# 3. Client Peer Extraction Tests
# ==============================================================================


def test_client_peer_extracts_from_x_forwarded_for():
    # Behind Render reverse proxy: client IP is leftmost in X-Forwarded-For
    ws = DummyWebSocket(
        client_host="10.0.0.1",
        forwarded_for="203.0.113.195, 10.0.0.1",
    )
    assert _client_peer(ws) == "203.0.113.195"
    assert presence_client_peer(ws) == "203.0.113.195"


def test_client_peer_single_forwarded_for():
    ws = DummyWebSocket(
        client_host="10.0.0.1",
        forwarded_for="198.51.100.42",
    )
    assert _client_peer(ws) == "198.51.100.42"
    assert presence_client_peer(ws) == "198.51.100.42"


def test_client_peer_falls_back_to_client_host():
    ws = DummyWebSocket(client_host="127.0.0.1", forwarded_for=None)
    assert _client_peer(ws) == "127.0.0.1"
    assert presence_client_peer(ws) == "127.0.0.1"


def test_client_peer_unknown_when_no_client():
    ws = DummyWebSocket(client_host=None, forwarded_for=None)
    assert _client_peer(ws) == "unknown"
    assert presence_client_peer(ws) == "unknown"


def _run_sync(coro):
    """Run a coroutine that completes synchronously without requiring a network event loop."""
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value
    raise RuntimeError("Coroutine did not complete synchronously")


# ==============================================================================
# 4. Connection Rate Limiter Tests
# ==============================================================================


def test_allow_connection_permits_under_limit():
    hub = ConnectionHub()
    hub.rate_limit_count = 5
    hub.rate_limit_window = 10.0

    for _ in range(5):
        assert _run_sync(hub.allow_connection("192.0.2.1")) is True


def test_allow_connection_blocks_when_limit_exceeded():
    hub = ConnectionHub()
    hub.rate_limit_count = 3
    hub.rate_limit_window = 60.0

    assert _run_sync(hub.allow_connection("192.0.2.1")) is True
    assert _run_sync(hub.allow_connection("192.0.2.1")) is True
    assert _run_sync(hub.allow_connection("192.0.2.1")) is True
    # 4th attempt should be blocked
    assert _run_sync(hub.allow_connection("192.0.2.1")) is False


def test_allow_connection_separate_buckets_per_peer():
    hub = ConnectionHub()
    hub.rate_limit_count = 2
    hub.rate_limit_window = 60.0

    # Exhaust peer A
    assert _run_sync(hub.allow_connection("peer_a")) is True
    assert _run_sync(hub.allow_connection("peer_a")) is True
    assert _run_sync(hub.allow_connection("peer_a")) is False

    # Peer B should still be allowed!
    assert _run_sync(hub.allow_connection("peer_b")) is True
    assert _run_sync(hub.allow_connection("peer_b")) is True
    assert _run_sync(hub.allow_connection("peer_b")) is False


# ==============================================================================
# 5. Diagnostic Logging Tests
# ==============================================================================


def test_websocket_endpoint_logs_warning_on_origin_rejection(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://quizverse-live-gamma.vercel.app")
    ws = DummyWebSocket(origin="https://disallowed-domain.com")

    with caplog.at_level(logging.WARNING, logger="quizverse.realtime"):
        if not _valid_origin(ws):
            raw_origin = ws.headers.get("origin")
            logging.getLogger("quizverse.realtime").warning(
                "WebSocket /ws handshake rejected: origin not allowed. origin=%r",
                raw_origin,
            )
            _run_sync(ws.close(code=1008, reason="Origin is not allowed"))

    assert ws.closed is True
    assert ws.close_code == 1008
    assert "origin not allowed" in caplog.text
    assert "https://disallowed-domain.com" in caplog.text


def test_websocket_endpoint_logs_warning_on_rate_limit(
    caplog: pytest.LogCaptureFixture,
):
    hub = ConnectionHub()
    hub.rate_limit_count = 1
    peer = "203.0.113.50"

    assert _run_sync(hub.allow_connection(peer)) is True

    ws = DummyWebSocket(origin="https://quizverse-live-gamma.vercel.app", forwarded_for=peer)
    with caplog.at_level(logging.WARNING, logger="quizverse.realtime"):
        if not _run_sync(hub.allow_connection(peer)):
            logging.getLogger("quizverse.realtime").warning(
                "WebSocket /ws handshake rejected: connection rate limit exceeded for peer=%s",
                peer,
            )
            _run_sync(ws.close(code=1008, reason="Too many connections"))

    assert ws.closed is True
    assert ws.close_code == 1008
    assert "rate limit exceeded" in caplog.text
    assert peer in caplog.text

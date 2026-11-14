"""FastAPI HTTP routes and WebSocket endpoint for presence authentication and real-time status."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import os
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Request, WebSocket, WebSocketDisconnect

from quizverse.persistence.models import utc_now
from quizverse.persistence.player_identity import (
    PlayerIdentityError,
    PlayerIdentityService,
    ProcessRateLimiter,
)
from quizverse.presence import PresenceService, PresenceTicketService
from quizverse.quiz_api import ApiProblem

logger = logging.getLogger(__name__)


def _allowed_origins() -> set[str]:
    configured = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
    return {origin.strip() for origin in configured.split(",") if origin.strip()}


def _valid_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    return origin in _allowed_origins()


def _authenticated_header(authorization: str | None) -> str:
    if authorization is None:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not 40 <= len(token.strip()) <= 128:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    return token.strip()


def install_presence_api(
    app: FastAPI,
    presence_service: PresenceService,
    tickets: PresenceTicketService,
    identities: PlayerIdentityService,
) -> None:
    router = APIRouter(prefix="/api/presence")
    ticket_limiter = ProcessRateLimiter(limit=20, window_seconds=60)

    async def get_current_player_id(authorization: str | None) -> str:
        token = _authenticated_header(authorization)
        try:
            auth = await asyncio.to_thread(identities.authenticate, token)
            return str(auth["id"])
        except PlayerIdentityError as error:
            raise ApiProblem(401, "invalid_player_credential", "The player credential is invalid or revoked.") from error

    @router.post("/ticket")
    async def issue_presence_ticket(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Issue a short-lived, single-use authentication ticket for the presence WebSocket."""
        current_player_id = await get_current_player_id(authorization)
        allowed, retry_after = ticket_limiter.allow(f"pticket:{current_player_id}")
        if not allowed:
            raise ApiProblem(429, "rate_limited", "Too many presence ticket requests.", headers={"Retry-After": str(retry_after)})

        raw_ticket = tickets.issue(current_player_id)
        return {
            "ticket": raw_ticket,
            "expires_in": int(tickets._ttl),
        }

    app.include_router(router)

    @app.websocket("/ws/presence")
    async def presence_websocket(websocket: WebSocket) -> None:
        if not _valid_origin(websocket):
            await websocket.close(code=1008, reason="Origin is not allowed")
            return

        peer = websocket.client.host if websocket.client else "unknown"
        if not await app.state.hub.allow_connection(peer):
            await websocket.close(code=1008, reason="Too many connections")
            return

        await websocket.accept()
        socket_id = secrets.token_hex(16)
        authenticated_player_id: str | None = None

        try:
            # 1. First event must be presence_auth within 10 seconds
            try:
                auth_raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "event": "presence_error",
                    "code": "auth_timeout",
                    "message": "Authentication timed out. Send presence_auth within 10s.",
                })
                await websocket.close(code=4001, reason="Authentication timeout")
                return

            try:
                auth_data = json.loads(auth_raw)
            except Exception:
                await websocket.send_json({
                    "event": "presence_error",
                    "code": "invalid_json",
                    "message": "Malformed JSON payload.",
                })
                await websocket.close(code=4001, reason="Invalid JSON")
                return

            if not isinstance(auth_data, dict) or auth_data.get("event") != "presence_auth":
                await websocket.send_json({
                    "event": "presence_error",
                    "code": "expected_auth",
                    "message": "First message must be a valid 'presence_auth' event.",
                })
                await websocket.close(code=4001, reason="Expected presence_auth")
                return

            ticket_value = auth_data.get("ticket")
            player_id = tickets.consume(ticket_value)
            if player_id is None:
                await websocket.send_json({
                    "event": "presence_error",
                    "code": "invalid_ticket",
                    "message": "The presence ticket is invalid, expired, or has already been consumed.",
                })
                await websocket.close(code=4001, reason="Invalid ticket")
                return

            if not await asyncio.to_thread(identities.is_active, player_id):
                await websocket.send_json({
                    "event": "presence_error",
                    "code": "player_inactive",
                    "message": "The player profile is inactive or unavailable.",
                })
                await websocket.close(code=4001, reason="Player inactive")
                return

            authenticated_player_id = player_id
            await presence_service.handle_connected(socket_id, player_id, websocket)

            # 2. Main presence event loop
            while True:
                raw_msg = await websocket.receive_text()
                try:
                    msg = json.loads(raw_msg)
                except Exception:
                    await websocket.send_json({
                        "event": "presence_error",
                        "code": "invalid_json",
                        "message": "Malformed JSON payload.",
                    })
                    continue

                if not isinstance(msg, dict):
                    await websocket.send_json({
                        "event": "presence_error",
                        "code": "invalid_event",
                        "message": "Message payload must be a JSON object.",
                    })
                    continue

                event_name = msg.get("event")
                if event_name == "ping":
                    await presence_service.handle_heartbeat(socket_id)
                    await websocket.send_json({
                        "event": "pong",
                        "timestamp": utc_now().isoformat(),
                    })
                else:
                    await websocket.send_json({
                        "event": "presence_error",
                        "code": "unsupported_event",
                        "message": f"Unsupported event: '{event_name}'.",
                    })

        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if authenticated_player_id is not None:
                await presence_service.handle_disconnected(socket_id)

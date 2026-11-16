"""FastAPI WebSocket transport for authenticated room events."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from quizverse.engine import GameEngine, GameError
from quizverse.questions import QUESTIONS
from quizverse.special_questions import BLUFF_QUESTIONS, ESCAPE_QUESTIONS
from quizverse.ai_generation import AIProvider, GenerationService
from quizverse.persistence.authorization import QuizAccessService
from quizverse.persistence.db import Database
from quizverse.persistence.player_identity import (
    LiveIdentityTickets,
    MatchHistoryService,
    PlayerIdentityService,
)
from quizverse.protocol import (
    AbilityEvent,
    AnswerEvent,
    BluffSubmitEvent,
    BluffVoteEvent,
    ClientEvent,
    CreateEvent,
    EscapeAnswerEvent,
    EscapeHintEvent,
    JoinEvent,
    LobbyEvent,
    ReconnectEvent,
    RematchEvent,
    RoomChatEvent,
    RoomChatReactionEvent,
    StartEvent,
    EVENT_ADAPTER,
)
from quizverse.quiz_api import install_quiz_api
from quizverse.taxonomy_api import install_taxonomy_api
from quizverse.exam_collection_api import install_exam_collection_api
from quizverse.player_api import install_player_api
from quizverse.persistence.tournaments import TournamentService
from quizverse.tournament_api import install_tournament_api
from quizverse.persistence.leagues import LeagueService
from quizverse.league_api import install_league_api
from quizverse.persistence.competition import (
    AdvancedStatsService,
    HeadToHeadService,
    HonorsService,
)
from quizverse.competition_api import install_competition_api
from quizverse.persistence.social import (
    BlockService,
    FriendService,
    PlayerDiscoveryService,
)
from quizverse.social_api import install_social_api
from quizverse.presence import (
    LocalMemoryPresenceBroker,
    PresenceService,
    PresenceTicketService,
)
from quizverse.presence_api import install_presence_api
from quizverse.persistence.invitations import InvitationService, RematchService
from quizverse.invitations_api import install_invitations_api
from quizverse.persistence.messaging import MessagingService
from quizverse.messaging_api import install_messaging_api
from quizverse.persistence.notifications import (
    NotificationService,
    SocialIntelligenceService,
)
from quizverse.notification_api import install_notification_api
from quizverse.intelligence import AIIntelligenceService
from quizverse.intelligence_api import register_intelligence_api
from quizverse.persistence.player_identity import ProcessRateLimiter

MAX_MESSAGE_BYTES = 4096
RATE_LIMIT_COUNT = 30
RATE_LIMIT_WINDOW = 10.0
CLIENT_DIRECTORY = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)


class ConnectionHub:
    """Maps active, authenticated player identities to exactly one socket."""

    def __init__(
        self,
        engine: GameEngine | None = None,
        *,
        identities: PlayerIdentityService | None = None,
        tickets: LiveIdentityTickets | None = None,
        match_history: MatchHistoryService | None = None,
    ) -> None:
        self.engine = engine or GameEngine()
        self.identities = identities
        self.tickets = tickets or LiveIdentityTickets()
        self.match_history = match_history
        self.presence_service: PresenceService | None = None
        self.invitation_ticket_validator: Any | None = None
        self.connections: dict[tuple[str, str], WebSocket] = {}
        self.peer_attempts: dict[str, deque[float]] = {}
        self.connection_lock = asyncio.Lock()

    def allow_origin(self, websocket: WebSocket) -> bool:
        return _valid_origin(websocket)

    async def allow_connection(self, peer: str) -> bool:
        now = time.monotonic()
        async with self.connection_lock:
            if len(self.peer_attempts) > 4096:
                stale_peers = [
                    address
                    for address, recent in self.peer_attempts.items()
                    if not recent or now - recent[-1] >= 60
                ]
                for address in stale_peers:
                    self.peer_attempts.pop(address, None)
            attempts = self.peer_attempts.setdefault(peer, deque())
            while attempts and now - attempts[0] >= 60:
                attempts.popleft()
            if len(attempts) >= 40:
                return False
            attempts.append(now)
            return True

    async def attach(self, identity: tuple[str, str], websocket: WebSocket) -> None:
        async with self.connection_lock:
            previous = self.connections.get(identity)
            self.connections[identity] = websocket
        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=4001, reason="Session moved to a new connection")
            except RuntimeError:
                pass
        if self.presence_service is not None:
            room = getattr(self.engine, "_rooms", {}).get(identity[0])
            if room:
                player = room.players.get(identity[1])
                if player and player.profile_id:
                    asyncio.create_task(self.presence_service.notify_activity_change([player.profile_id]))

    async def detach(self, identity: tuple[str, str], websocket: WebSocket) -> bool:
        async with self.connection_lock:
            if self.connections.get(identity) is not websocket:
                return False
            del self.connections[identity]
        if self.presence_service is not None:
            room = getattr(self.engine, "_rooms", {}).get(identity[0])
            if room:
                player = room.players.get(identity[1])
                if player and player.profile_id:
                    asyncio.create_task(self.presence_service.notify_activity_change([player.profile_id]))
        return True

    async def send(self, websocket: WebSocket, event: str, state: dict[str, Any] | None = None, **extra: Any) -> None:
        payload: dict[str, Any] = {"event": event, **extra}
        if state is not None:
            payload["state"] = state
        try:
            await websocket.send_json(payload)
        except (RuntimeError, WebSocketDisconnect):
            pass

    async def broadcast_room(self, code: str, event: str) -> None:
        async with self.connection_lock:
            recipients = [(identity, websocket) for identity, websocket in self.connections.items() if identity[0] == code]
        for (_, player_id), websocket in recipients:
            try:
                state = await self.engine.snapshot(code, player_id)
            except GameError:
                continue
            await self.send(websocket, event, state)

    async def broadcast_room_event(self, code: str, event: str, **payload: Any) -> None:
        async with self.connection_lock:
            recipients = [ws for identity, ws in self.connections.items() if identity[0] == code]
        for ws in recipients:
            try:
                await ws.send_json({"event": event, **payload})
            except (RuntimeError, WebSocketDisconnect):
                pass

    async def _profile_for_ticket(self, ticket: str | None) -> str | None:
        if ticket is None:
            return None
        player_id = self.tickets.consume(ticket)
        if player_id is None or self.identities is None:
            raise GameError("invalid_identity_ticket", "The player sign-in ticket is invalid or expired. Sign in again.")
        if not await asyncio.to_thread(self.identities.is_active, player_id):
            raise GameError("invalid_identity_ticket", "The player profile is unavailable. Sign in again.")
        return player_id

    async def _persist_completed_matches(self) -> None:
        if self.match_history is None:
            return
        for result in await self.engine.claim_completed_match_results():
            match_id = str(result["engine_match_id"])
            try:
                await asyncio.to_thread(self.match_history.persist_completed, result)
            except Exception as error:
                logger.error(
                    "Completed profile-linked match persistence failed; retry scheduled. engine_match_id=%s error_type=%s",
                    match_id,
                    type(error).__name__,
                )
                code = await self.engine.mark_match_result_failed(match_id, "persistence_unavailable")
            else:
                code = await self.engine.mark_match_result_saved(match_id)
            if code:
                await self.broadcast_room(code, "match-history-status")

    async def scheduler(self, stop: asyncio.Event) -> None:
        """Advance authoritative deadlines and expire idle memberships."""
        last_cleanup = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.1)
            states = await self.engine.advance_due()
            for code in dict.fromkeys(str(state["code"]) for state in states):
                await self.broadcast_room(code, "state")
            if self.presence_service is not None and states:
                pids = [
                    p.profile_id
                    for r in getattr(self.engine, "_rooms", {}).values()
                    for p in r.players.values()
                    if p.profile_id
                ]
                if pids:
                    await self.presence_service.notify_activity_change(pids)
            await self._persist_completed_matches()
            if time.monotonic() - last_cleanup >= 10:
                removed_rooms = await self.engine.cleanup()
                for code in await self.engine.room_codes():
                    await self.broadcast_room(code, "player-expired")
                last_cleanup = time.monotonic()
                if self.presence_service is not None:
                    await self.presence_service.maintenance_step()
                # A removed room has no remaining authorized recipients; ``removed_rooms`` is
                # deliberately retained here for observability while avoiding phantom events.
                _ = removed_rooms


def _allowed_origins() -> set[str]:
    configured = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
    return {origin.strip() for origin in configured.split(",") if origin.strip()}


def _valid_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if origin is None:
        return True  # Native non-browser clients have no Origin header.
    return origin in _allowed_origins()


def _parse_event(raw: str) -> ClientEvent:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GameError("invalid_json", "Send a valid JSON event.") from exc
    try:
        return EVENT_ADAPTER.validate_python(value)
    except (ValidationError, TypeError) as exc:
        raise GameError("invalid_event", "That event is invalid. Check its name and fields.") from exc


def create_app(
    engine: GameEngine | None = None,
    database: Database | None = None,
    ai_provider: AIProvider | None = None,
) -> FastAPI:
    persistence = database or Database()
    owns_database = database is None
    quiz_access = QuizAccessService(persistence)

    def load_game_questions(selection: dict[str, object]):
        mode = selection.get("mode")
        question_format = "open_ended" if mode == "bluff_master" else "puzzle" if mode == "escape_room" else "multiple_choice"
        try:
            if selection.get("content_scope", "built_in") == "built_in":
                try:
                    inventory = quiz_access.built_in_game_questions()
                except SQLAlchemyError:
                    inventory = QUESTIONS
                return inventory + BLUFF_QUESTIONS + ESCAPE_QUESTIONS
            return quiz_access.game_question_source(
                quiz_ids=tuple(selection.get("quiz_ids", ())),
                capability=selection.get("library_capability") if isinstance(selection.get("library_capability"), str) else None,
                public_library=selection.get("content_scope") == "public_library",
                question_format=question_format,
            )
        except ValueError as error:
            raise ValueError(str(error)) from error
        except SQLAlchemyError as error:
            raise ValueError("Quiz storage is temporarily unavailable. Try starting this match again.") from error

    active_engine = engine or GameEngine()
    active_engine.set_question_provider(load_game_questions)
    player_identities = PlayerIdentityService(persistence)
    player_tickets = LiveIdentityTickets()
    tournament_service = TournamentService(persistence, engine=active_engine)
    league_service = LeagueService(persistence, engine=active_engine)
    match_history = MatchHistoryService(persistence, tournament_service=tournament_service, league_service=league_service)
    hub = ConnectionHub(active_engine, identities=player_identities, tickets=player_tickets, match_history=match_history)

    stats_service = AdvancedStatsService(persistence)
    h2h_service = HeadToHeadService(persistence)
    honors_service = HonorsService(persistence)
    block_service = BlockService(persistence)
    friend_service = FriendService(persistence, block_service, match_history.rating_service)
    discovery_service = PlayerDiscoveryService(persistence, friend_service, block_service, stats_service, honors_service)

    presence_tickets = PresenceTicketService()
    presence_broker = LocalMemoryPresenceBroker()
    presence_service = PresenceService(
        broker=presence_broker,
        engine=active_engine,
        friend_service=friend_service,
        block_service=block_service,
        tickets=presence_tickets,
        identities=player_identities,
    )
    friend_service.presence_provider = presence_service.get_cached_state

    invitation_service = InvitationService(
        database=persistence,
        friend_service=friend_service,
        block_service=block_service,
        presence_service=presence_service,
    )
    rematch_service = RematchService(
        database=persistence,
        friend_service=friend_service,
        block_service=block_service,
        presence_service=presence_service,
    )
    messaging_service = MessagingService(
        database=persistence,
        friend_service=friend_service,
        block_service=block_service,
    )
    notification_service = NotificationService(
        database=persistence,
        block_service=block_service,
        friend_service=friend_service,
        presence_service=presence_service,
    )
    social_intelligence_service = SocialIntelligenceService(
        database=persistence,
        friend_service=friend_service,
        block_service=block_service,
        presence_service=presence_service,
    )
    friend_service.notification_service = notification_service
    invitation_service.notification_service = notification_service
    rematch_service.notification_service = notification_service
    messaging_service.notification_service = notification_service
    tournament_service.notification_service = notification_service
    league_service.notification_service = notification_service
    hub.invitation_ticket_validator = invitation_service.validate_and_consume_ticket

    def _on_friendship_severed(p1: str, p2: str) -> None:
        try:
            invitation_service.cancel_pending_between_players(p1, p2)
        except Exception:
            pass
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(presence_service.notify_friendship_severed(p1, p2))
        except RuntimeError:
            pass

    friend_service.on_friendship_severed = _on_friendship_severed
    block_service.on_friendship_severed = _on_friendship_severed
    hub.presence_service = presence_service

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        presence_service.loop = asyncio.get_running_loop()
        try:
            reconciled_match_ids = await asyncio.to_thread(match_history.rating_service.reconcile_unrated_matches)
            if reconciled_match_ids:
                logger.info("Reconciled previously stored competitive matches into Elo ratings. match_count=%s", len(reconciled_match_ids))
        except Exception as error:
            logger.error("Stored match rating reconciliation failed; it will retry on the next startup. error_type=%s", type(error).__name__)
        stop = asyncio.Event()
        task = asyncio.create_task(hub.scheduler(stop), name="quizverse-room-scheduler")
        presence_task = asyncio.create_task(presence_service.maintenance_loop(stop), name="quizverse-presence-maintenance")
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            presence_task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            try:
                await presence_task
            except asyncio.CancelledError:
                pass
            await presence_service.shutdown()
            if owns_database:
                persistence.close()

    app = FastAPI(title="QuizVerse Live", version="0.1.0", lifespan=lifespan)
    app.state.hub = hub
    app.state.engine = active_engine
    app.state.database = persistence
    app.state.rating_service = match_history.rating_service
    app.state.tournament_service = tournament_service
    app.state.league_service = league_service
    app.state.advanced_stats_service = stats_service
    app.state.head_to_head_service = h2h_service
    app.state.honors_service = honors_service
    app.state.block_service = block_service
    app.state.friend_service = friend_service
    app.state.discovery_service = discovery_service
    app.state.presence_service = presence_service
    app.state.presence_tickets = presence_tickets
    app.state.invitation_service = invitation_service
    app.state.rematch_service = rematch_service
    app.state.messaging_service = messaging_service
    app.state.notification_service = notification_service
    app.state.social_intelligence_service = social_intelligence_service

    ai_gen_svc = GenerationService(ai_provider)
    intelligence_service = AIIntelligenceService(
        database=persistence,
        ai_service=ai_gen_svc,
    )
    app.state.intelligence_service = intelligence_service

    install_quiz_api(app, quiz_access, ai_gen_svc)
    install_player_api(app, player_identities, match_history, player_tickets)
    install_taxonomy_api(app, persistence)
    install_exam_collection_api(app, persistence)
    install_tournament_api(app, tournament_service, player_identities, active_engine)
    install_league_api(app, league_service, player_identities, active_engine)
    install_competition_api(app, stats_service, h2h_service, honors_service, player_identities)
    install_social_api(app, discovery_service, friend_service, block_service, player_identities)
    install_presence_api(app, presence_service, presence_tickets, player_identities)
    install_invitations_api(app, invitation_service, rematch_service, player_identities, active_engine)
    install_messaging_api(app, messaging_service, player_identities, presence_service)
    install_notification_api(app, notification_service, social_intelligence_service, player_identities, presence_service)
    register_intelligence_api(app, intelligence_service, player_identities)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str | bool | int]:
        persistence_health = await active_engine.match_history_persistence_health()
        return {"ok": True, "service": "quizverse-live", **persistence_health}

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["content-security-policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; img-src 'self' data:; font-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' ws: wss:"
        )
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "strict-origin-when-cross-origin"
        response.headers["x-frame-options"] = "DENY"
        response.headers["permissions-policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    room_chat_limiter = ProcessRateLimiter(limit=10, window_seconds=10)
    room_chat_reaction_limiter = ProcessRateLimiter(limit=20, window_seconds=60)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        if not _valid_origin(websocket):
            await websocket.close(code=1008, reason="Origin is not allowed")
            return
        peer = websocket.client.host if websocket.client else "unknown"
        if not await hub.allow_connection(peer):
            await websocket.close(code=1008, reason="Too many connections")
            return
        await websocket.accept()
        identity: tuple[str, str] | None = None
        received_at: deque[float] = deque()
        try:
            while True:
                raw = await websocket.receive_text()
                if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                    await hub.send(websocket, "error", code="message_too_large", message="Message limit is 4 KB.")
                    await websocket.close(code=1009, reason="Message too large")
                    break
                now = time.monotonic()
                while received_at and now - received_at[0] > RATE_LIMIT_WINDOW:
                    received_at.popleft()
                if len(received_at) >= RATE_LIMIT_COUNT:
                    await hub.send(websocket, "error", code="rate_limited", message="Too many events. Please slow down.")
                    await websocket.close(code=1008, reason="Rate limit exceeded")
                    break
                received_at.append(now)
                try:
                    event = _parse_event(raw)
                    if isinstance(event, CreateEvent):
                        if identity is not None:
                            raise GameError("already_joined", "This connection already belongs to an arena.")
                        profile_id = await hub._profile_for_ticket(event.identity_ticket)
                        joined = await hub.engine.create_room(
                            event.name,
                            mode=event.mode,
                            selection=event.model_dump(exclude={"event", "name", "mode", "identity_ticket"}, exclude_none=True),
                            profile_id=profile_id,
                        )
                        identity = (str(joined["state"]["code"]), str(joined["player_id"]))
                        await hub.attach(identity, websocket)
                        await hub.send(websocket, "joined", joined["state"], token=joined["token"], player_id=joined["player_id"])
                    elif isinstance(event, JoinEvent):
                        if identity is not None:
                            raise GameError("already_joined", "This connection already belongs to an arena.")
                        profile_id = await hub._profile_for_ticket(event.identity_ticket)
                        joined = await hub.engine.join_room(
                            event.code,
                            event.name,
                            profile_id=profile_id,
                            admission_ticket=event.admission_ticket,
                            ticket_validator=hub.invitation_ticket_validator,
                        )
                        identity = (str(joined["state"]["code"]), str(joined["player_id"]))
                        await hub.attach(identity, websocket)
                        await hub.send(websocket, "joined", joined["state"], token=joined["token"], player_id=joined["player_id"])
                        await hub.broadcast_room(identity[0], "player-joined")
                    elif isinstance(event, ReconnectEvent):
                        if identity is not None:
                            raise GameError("already_joined", "This connection already belongs to an arena.")
                        joined = await hub.engine.reconnect(event.token)
                        identity = (str(joined["state"]["code"]), str(joined["player_id"]))
                        await hub.attach(identity, websocket)
                        await hub.send(websocket, "joined", joined["state"], token=joined["token"], player_id=joined["player_id"])
                        await hub.broadcast_room(identity[0], "player-reconnected")
                    else:
                        if identity is None:
                            raise GameError("join_first", "Create or join an arena first.")
                        code, player_id = identity
                        if isinstance(event, StartEvent):
                            await hub.engine.start_game(code, player_id)
                            await hub.broadcast_room(code, "state")
                        elif isinstance(event, AnswerEvent):
                            await hub.engine.submit_answer(code, player_id, event.choice)
                            await hub.broadcast_room(code, "answer-update")
                        elif isinstance(event, AbilityEvent):
                            await hub.engine.activate_ability(code, player_id, event.ability, event.request_id)
                            await hub.broadcast_room(code, "ability-update")
                        elif isinstance(event, BluffSubmitEvent):
                            await hub.engine.submit_bluff(code, player_id, event.answer, event.request_id)
                            await hub.broadcast_room(code, "bluff-submit")
                        elif isinstance(event, BluffVoteEvent):
                            await hub.engine.cast_bluff_vote(code, player_id, event.choice_id, event.request_id)
                            await hub.broadcast_room(code, "bluff-vote")
                        elif isinstance(event, EscapeAnswerEvent):
                            await hub.engine.submit_escape_answer(code, player_id, event.answer, event.puzzle_index, event.request_id)
                            await hub.broadcast_room(code, "escape-answer")
                        elif isinstance(event, EscapeHintEvent):
                            await hub.engine.request_escape_hint(code, player_id, event.request_id)
                            await hub.broadcast_room(code, "escape-hint")
                        elif isinstance(event, (RematchEvent, LobbyEvent)):
                            state = await hub.engine.snapshot(code, player_id)
                            if state["phase"] != "results":
                                raise GameError("wrong_phase", "Finish the quiz before returning to the lobby.")
                            await hub.engine.set_lobby(code, player_id, rematch=isinstance(event, RematchEvent))
                            await hub.broadcast_room(code, "lobby")
                        elif isinstance(event, RoomChatEvent):
                            allowed, retry_after = room_chat_limiter.allow(player_id)
                            if not allowed:
                                raise GameError("rate_limited", f"Chat rate limit exceeded. Please wait {retry_after}s.")
                            msg = await hub.engine.send_room_chat_message(code, player_id, event.text)
                            await hub.broadcast_room_event(code, "room_chat_message", **msg)
                            await hub.broadcast_room(code, "room-chat-update")
                        elif isinstance(event, RoomChatReactionEvent):
                            allowed, retry_after = room_chat_reaction_limiter.allow(player_id)
                            if not allowed:
                                raise GameError("rate_limited", f"Reaction rate limit exceeded. Please wait {retry_after}s.")
                            rx_res = await hub.engine.add_room_chat_reaction(code, player_id, event.message_id, event.reaction)
                            await hub.broadcast_room_event(code, "room_chat_reaction_updated", **rx_res)
                            await hub.broadcast_room(code, "room-chat-update")
                except GameError as exc:
                    await hub.send(websocket, "error", code=exc.code, message=exc.message)
                    if identity is not None and exc.state_changed:
                        await hub.broadcast_room(identity[0], "state")
        except WebSocketDisconnect:
            pass
        finally:
            if identity is not None and await hub.detach(identity, websocket):
                try:
                    await hub.engine.disconnect(*identity)
                    await hub.broadcast_room(identity[0], "player-left")
                except GameError:
                    pass

    app.mount("/", StaticFiles(directory=CLIENT_DIRECTORY, html=True), name="client")
    return app


app = create_app()

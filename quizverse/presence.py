"""Authoritative real-time presence subsystem: brokers, heartbeat tracking, activity states, and friend broadcasts."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from quizverse.persistence.models import utc_now
from quizverse.persistence.player_identity import PlayerIdentityService
from quizverse.persistence.social import BlockService, FriendService

logger = logging.getLogger(__name__)


class PresenceState:
    ONLINE = "online"
    IN_LOBBY = "in_lobby"
    IN_MATCH = "in_match"
    OFFLINE = "offline"


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PresenceTicketService:
    """Issues single-use, short-lived authentication tickets for the presence WebSocket."""

    def __init__(self, *, ttl_seconds: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._tickets: dict[str, tuple[str, float]] = {}  # hash -> (player_id, expires_at)
        self._lock = threading.Lock()

    def issue(self, player_id: str) -> str:
        raw = secrets.token_urlsafe(48)
        now = self._clock()
        digest = _hash_token(raw)
        with self._lock:
            # Prune expired
            self._tickets = {k: v for k, v in self._tickets.items() if v[1] > now}
            self._tickets[digest] = (player_id, now + self._ttl)
        return raw

    def consume(self, raw_ticket: object) -> str | None:
        if not isinstance(raw_ticket, str) or not 40 <= len(raw_ticket) <= 128:
            return None
        try:
            digest = _hash_token(raw_ticket)
        except Exception:
            return None
        now = self._clock()
        with self._lock:
            item = self._tickets.pop(digest, None)
        if item is None or item[1] <= now:
            return None
        return item[0]


@dataclass(slots=True)
class SocketRecord:
    socket_id: str
    player_id: str
    websocket: Any
    connected_at: float
    last_heartbeat: float


class PresenceBroker(Protocol):
    """Abstract presence broker contract for single-process or distributed backends."""

    async def register_socket(self, socket_id: str, player_id: str, websocket: Any) -> None: ...
    async def unregister_socket(self, socket_id: str) -> tuple[str, int] | None: ...
    async def record_heartbeat(self, socket_id: str) -> bool: ...
    async def get_player_socket_count(self, player_id: str) -> int: ...
    async def is_player_connected(self, player_id: str) -> bool: ...
    async def get_all_connected_player_ids(self) -> set[str]: ...
    async def cleanup_stale_sockets(self, max_idle_seconds: float) -> list[tuple[str, str, Any, int]]: ...
    async def send_to_player(self, player_id: str, payload: dict[str, Any]) -> None: ...


class LocalMemoryPresenceBroker:
    """In-process, single-instance presence broker implementation."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._sockets: dict[str, SocketRecord] = {}  # socket_id -> SocketRecord
        self._player_sockets: dict[str, set[str]] = {}  # player_id -> set of socket_ids
        self._lock = asyncio.Lock()

    async def register_socket(self, socket_id: str, player_id: str, websocket: Any) -> None:
        now = self._clock()
        async with self._lock:
            record = SocketRecord(
                socket_id=socket_id,
                player_id=player_id,
                websocket=websocket,
                connected_at=now,
                last_heartbeat=now,
            )
            self._sockets[socket_id] = record
            self._player_sockets.setdefault(player_id, set()).add(socket_id)

    async def unregister_socket(self, socket_id: str) -> tuple[str, int] | None:
        async with self._lock:
            record = self._sockets.pop(socket_id, None)
            if record is None:
                return None
            player_id = record.player_id
            sockets = self._player_sockets.get(player_id)
            if sockets is not None:
                sockets.discard(socket_id)
                remaining = len(sockets)
                if remaining == 0:
                    del self._player_sockets[player_id]
            else:
                remaining = 0
            return player_id, remaining

    async def record_heartbeat(self, socket_id: str) -> bool:
        now = self._clock()
        async with self._lock:
            record = self._sockets.get(socket_id)
            if record is None:
                return False
            record.last_heartbeat = now
            return True

    async def get_player_socket_count(self, player_id: str) -> int:
        async with self._lock:
            return len(self._player_sockets.get(player_id, set()))

    async def is_player_connected(self, player_id: str) -> bool:
        async with self._lock:
            return len(self._player_sockets.get(player_id, set())) > 0

    async def get_all_connected_player_ids(self) -> set[str]:
        async with self._lock:
            return set(self._player_sockets.keys())

    async def cleanup_stale_sockets(self, max_idle_seconds: float) -> list[tuple[str, str, Any, int]]:
        now = self._clock()
        stale_records: list[tuple[str, str, Any, int]] = []
        async with self._lock:
            stale_ids = [
                s_id
                for s_id, rec in self._sockets.items()
                if now - rec.last_heartbeat > max_idle_seconds
            ]
            for s_id in stale_ids:
                rec = self._sockets.pop(s_id, None)
                if rec is not None:
                    player_id = rec.player_id
                    sockets = self._player_sockets.get(player_id)
                    if sockets is not None:
                        sockets.discard(s_id)
                        remaining = len(sockets)
                        if remaining == 0:
                            del self._player_sockets[player_id]
                    else:
                        remaining = 0
                    stale_records.append((s_id, player_id, rec.websocket, remaining))
        return stale_records

    async def send_to_player(self, player_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            socket_ids = list(self._player_sockets.get(player_id, ()))
            websockets = [self._sockets[sid].websocket for sid in socket_ids if sid in self._sockets]
        for ws in websockets:
            try:
                await ws.send_json(payload)
            except Exception:
                pass


class PresenceService:
    """Authoritative server-side presence coordinator for lifecycle, activity, and notifications."""

    def __init__(
        self,
        broker: PresenceBroker,
        engine: Any,
        friend_service: FriendService,
        block_service: BlockService,
        tickets: PresenceTicketService,
        identities: PlayerIdentityService | None = None,
        *,
        heartbeat_interval: float = 25.0,
        stale_threshold: float = 60.0,
        grace_period: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        import os
        self.broker = broker
        self.engine = engine
        self.friend_service = friend_service
        self.block_service = block_service
        self.tickets = tickets
        self.identities = identities
        self.heartbeat_interval = heartbeat_interval
        self.stale_threshold = stale_threshold
        self.grace_period = grace_period if grace_period is not None else float(os.getenv("PRESENCE_GRACE_PERIOD", "10.0"))
        self.clock = clock

        self._grace_tasks: dict[str, asyncio.Task] = {}
        self._current_states: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None

    def safe_dispatch(self, coro: Any) -> None:
        """Safely schedule a coroutine onto the application event loop from any thread."""
        loop = self.loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
        if loop.is_closed():
            return
        try:
            current_loop = asyncio.get_running_loop()
            if current_loop is loop:
                loop.create_task(coro)
                return
        except RuntimeError:
            pass
        asyncio.run_coroutine_threadsafe(coro, loop)

    async def determine_player_state(self, player_id: str) -> str:
        """Derive the player's true presence state from socket connectivity and GameEngine room state."""
        connected = await self.broker.is_player_connected(player_id)
        has_grace = player_id in self._grace_tasks

        if not connected and not has_grace:
            return PresenceState.OFFLINE

        # Check server-authoritative activity state from GameEngine
        activity = None
        if hasattr(self.engine, "get_player_activity"):
            activity = await self.engine.get_player_activity(player_id)

        if activity in (PresenceState.IN_LOBBY, PresenceState.IN_MATCH):
            return activity

        return PresenceState.ONLINE

    def get_cached_state(self, player_id: str) -> str:
        return self._current_states.get(player_id, PresenceState.OFFLINE)

    async def get_friends_presence_snapshot(self, player_id: str) -> list[dict[str, Any]]:
        """Return presence snapshot of accepted, non-blocked friends."""
        friends = await asyncio.to_thread(self.friend_service.list_friends, player_id)
        if not friends:
            return []

        friend_ids = [f["player_id"] for f in friends]
        # Query activity states in batch
        activity_map: dict[str, str] = {}
        if hasattr(self.engine, "get_active_profiles_activity"):
            activity_map = await self.engine.get_active_profiles_activity(friend_ids)

        snapshot: list[dict[str, Any]] = []
        now_iso = utc_now().isoformat()
        for f in friends:
            fid = f["player_id"]
            connected = await self.broker.is_player_connected(fid)
            has_grace = fid in self._grace_tasks
            if not connected and not has_grace:
                status = PresenceState.OFFLINE
            else:
                act = activity_map.get(fid)
                if act in (PresenceState.IN_LOBBY, PresenceState.IN_MATCH):
                    status = act
                else:
                    status = PresenceState.ONLINE

            snapshot.append({
                "player_id": fid,
                "display_name": f["display_name"],
                "status": status,
                "updated_at": now_iso,
            })
        return snapshot

    async def handle_connected(self, socket_id: str, player_id: str, websocket: Any) -> None:
        """Register newly authenticated socket, cancel any pending grace timer, and notify friends."""
        await self.broker.register_socket(socket_id, player_id, websocket)

        # Cancel grace task if one was active for this player
        async with self._lock:
            grace_task = self._grace_tasks.pop(player_id, None)
            if grace_task is not None and not grace_task.done():
                grace_task.cancel()

        new_state = await self.determine_player_state(player_id)
        old_state = self._current_states.get(player_id, PresenceState.OFFLINE)
        self._current_states[player_id] = new_state

        if new_state != old_state:
            await self.broadcast_presence_to_friends(player_id, new_state)

        # Deliver initial presence snapshot to this socket
        snapshot = await self.get_friends_presence_snapshot(player_id)
        try:
            await websocket.send_json({
                "event": "presence_ready",
                "player_id": player_id,
                "snapshot": snapshot,
                "timestamp": utc_now().isoformat(),
            })
        except Exception:
            pass

    async def handle_disconnected(self, socket_id: str) -> None:
        """Unregister socket and initiate grace timer if last socket closed."""
        result = await self.broker.unregister_socket(socket_id)
        if result is None:
            return
        player_id, remaining_count = result
        if remaining_count == 0:
            async with self._lock:
                if player_id not in self._grace_tasks:
                    task = asyncio.create_task(self._run_grace_period(player_id))
                    self._grace_tasks[player_id] = task

    async def _run_grace_period(self, player_id: str) -> None:
        try:
            await asyncio.sleep(self.grace_period)
            connected = await self.broker.is_player_connected(player_id)
            if not connected:
                self._current_states[player_id] = PresenceState.OFFLINE
                await self.broadcast_presence_to_friends(player_id, PresenceState.OFFLINE)
        except asyncio.CancelledError:
            # Reconnected during grace period!
            pass
        finally:
            async with self._lock:
                self._grace_tasks.pop(player_id, None)

    async def handle_heartbeat(self, socket_id: str) -> bool:
        return await self.broker.record_heartbeat(socket_id)

    async def broadcast_presence_to_friends(self, player_id: str, status: str) -> None:
        """Broadcast authoritative presence transition exclusively to accepted, non-blocked online friends."""
        try:
            friends = await asyncio.to_thread(self.friend_service.list_friends, player_id)
        except Exception as err:
            logger.warning("Failed to query friends for presence broadcast: %s", err)
            return

        if not friends:
            return

        payload = {
            "event": "presence_update",
            "player_id": player_id,
            "status": status,
            "updated_at": utc_now().isoformat(),
        }

        for f in friends:
            friend_id = f["player_id"]
            if await self.broker.is_player_connected(friend_id):
                await self.broker.send_to_player(friend_id, payload)

    async def notify_activity_change(self, player_ids: Iterable[str]) -> None:
        """Recalculate presence state for players whose room or match activity changed."""
        unique_ids = set(player_ids)
        for pid in unique_ids:
            new_state = await self.determine_player_state(pid)
            old_state = self._current_states.get(pid, PresenceState.OFFLINE)
            if new_state != old_state:
                self._current_states[pid] = new_state
                await self.broadcast_presence_to_friends(pid, new_state)

    async def notify_friendship_severed(self, p1: str, p2: str) -> None:
        """Notify both players that the other is offline immediately upon un-friending or blocking."""
        now_iso = utc_now().isoformat()
        if await self.broker.is_player_connected(p1):
            await self.broker.send_to_player(p1, {
                "event": "presence_update",
                "player_id": p2,
                "status": PresenceState.OFFLINE,
                "updated_at": now_iso,
            })
        if await self.broker.is_player_connected(p2):
            await self.broker.send_to_player(p2, {
                "event": "presence_update",
                "player_id": p1,
                "status": PresenceState.OFFLINE,
                "updated_at": now_iso,
            })

    async def send_invitation_notification(self, invitee_id: str, invitation: dict[str, Any]) -> None:
        """Deliver real-time room invitation event to invitee's active presence sockets."""
        if await self.broker.is_player_connected(invitee_id):
            await self.broker.send_to_player(invitee_id, {
                "event": "room_invitation_received",
                **invitation,
            })

    async def send_rematch_notification(self, opponent_id: str, challenge: dict[str, Any]) -> None:
        """Deliver real-time rematch challenge event to opponent's active presence sockets."""
        if await self.broker.is_player_connected(opponent_id):
            await self.broker.send_to_player(opponent_id, {
                "event": "rematch_challenge_received",
                **challenge,
            })

    async def send_rematch_accepted_notification(self, challenger_id: str, payload: dict[str, Any]) -> None:
        """Deliver real-time rematch accepted event to challenger's active presence sockets."""
        if await self.broker.is_player_connected(challenger_id):
            await self.broker.send_to_player(challenger_id, {
                "event": "rematch_challenge_accepted",
                **payload,
            })

    async def send_direct_message_notification(self, recipient_id: str, payload: dict[str, Any]) -> bool:
        """Deliver real-time direct message event to recipient's active presence sockets. Returns True if delivered."""
        if await self.broker.is_player_connected(recipient_id):
            await self.broker.send_to_player(recipient_id, {
                "event": "direct_message_received",
                **payload,
            })
            return True
        return False

    async def send_direct_message_delivery_receipt(self, sender_id: str, payload: dict[str, Any]) -> None:
        """Notify sender that message was delivered to an active recipient socket."""
        if await self.broker.is_player_connected(sender_id):
            await self.broker.send_to_player(sender_id, {
                "event": "direct_message_delivered",
                **payload,
            })

    async def send_direct_message_read_receipt(self, sender_id: str, payload: dict[str, Any]) -> None:
        """Notify sender that recipient marked message(s) as read."""
        if await self.broker.is_player_connected(sender_id):
            await self.broker.send_to_player(sender_id, {
                "event": "direct_message_read",
                **payload,
            })

    async def send_direct_message_edited(self, recipient_id: str, payload: dict[str, Any]) -> None:
        """Notify recipient that a message was edited."""
        if await self.broker.is_player_connected(recipient_id):
            await self.broker.send_to_player(recipient_id, {
                "event": "direct_message_edited",
                **payload,
            })

    async def send_direct_message_deleted(self, recipient_id: str, payload: dict[str, Any]) -> None:
        """Notify recipient that a message was deleted."""
        if await self.broker.is_player_connected(recipient_id):
            await self.broker.send_to_player(recipient_id, {
                "event": "direct_message_deleted",
                **payload,
            })

    async def send_direct_message_reaction(self, player_ids: Iterable[str], payload: dict[str, Any]) -> None:
        """Broadcast reaction updates to conversation participants."""
        for pid in set(player_ids):
            if await self.broker.is_player_connected(pid):
                await self.broker.send_to_player(pid, {
                    "event": "direct_message_reaction_updated",
                    **payload,
                })

    async def send_typing_indicator(self, recipient_id: str, payload: dict[str, Any]) -> None:
        """Deliver ephemeral typing event to recipient."""
        if await self.broker.is_player_connected(recipient_id):
            await self.broker.send_to_player(recipient_id, payload)

    async def send_notification_received(self, recipient_id: str, notification: dict[str, Any]) -> bool:
        """Deliver real-time notification to authenticated recipient's active presence sockets."""
        if await self.broker.is_player_connected(recipient_id):
            await self.broker.send_to_player(recipient_id, {
                "event": "notification_received",
                "notification": notification,
            })
            return True
        return False

    async def send_notification_read(self, recipient_id: str, payload: dict[str, Any]) -> None:
        """Deliver real-time notification read status to recipient sockets."""
        if await self.broker.is_player_connected(recipient_id):
            await self.broker.send_to_player(recipient_id, {
                "event": "notification_read",
                **payload,
            })

    async def send_notification_deleted(self, recipient_id: str, payload: dict[str, Any]) -> None:
        """Deliver real-time notification deletion to recipient sockets."""
        if await self.broker.is_player_connected(recipient_id):
            await self.broker.send_to_player(recipient_id, {
                "event": "notification_deleted",
                **payload,
            })

    async def send_notification_unread_count(self, recipient_id: str, count: int) -> None:
        """Deliver real-time unread notification count update to recipient sockets."""
        if await self.broker.is_player_connected(recipient_id):
            await self.broker.send_to_player(recipient_id, {
                "event": "notification_unread_count",
                "unread_count": count,
            })

    async def send_notification_preferences_updated(self, player_id: str, preferences: dict[str, Any]) -> None:
        """Deliver real-time notification preferences update to player sockets."""
        if await self.broker.is_player_connected(player_id):
            await self.broker.send_to_player(player_id, {
                "event": "notification_preferences_updated",
                "preferences": preferences,
            })



    async def maintenance_step(self) -> None:
        """Clean up idle sockets exceeding the stale threshold and reconcile activity transitions."""
        stale = await self.broker.cleanup_stale_sockets(self.stale_threshold)
        for s_id, pid, ws, remaining in stale:
            try:
                await ws.close(code=1000, reason="Presence heartbeat timeout")
            except Exception:
                pass
            if remaining == 0:
                async with self._lock:
                    if pid not in self._grace_tasks:
                        task = asyncio.create_task(self._run_grace_period(pid))
                        self._grace_tasks[pid] = task

        # Reconcile activity states of connected players
        connected_ids = await self.broker.get_all_connected_player_ids()
        if connected_ids:
            activity_map = {}
            if hasattr(self.engine, "get_active_profiles_activity"):
                activity_map = await self.engine.get_active_profiles_activity(connected_ids)
            for pid in connected_ids:
                act = activity_map.get(pid)
                expected = act if act in (PresenceState.IN_LOBBY, PresenceState.IN_MATCH) else PresenceState.ONLINE
                curr = self._current_states.get(pid)
                if curr != expected:
                    self._current_states[pid] = expected
                    await self.broadcast_presence_to_friends(pid, expected)

    async def maintenance_loop(self, stop_event: asyncio.Event) -> None:
        """Periodic background task that runs maintenance every 5.0 seconds."""
        self.loop = asyncio.get_running_loop()
        while not stop_event.is_set():
            try:
                await asyncio.sleep(5.0)
                if stop_event.is_set():
                    break
                await self.maintenance_step()
            except asyncio.CancelledError:
                break
            except Exception as err:
                logger.error("Error in presence maintenance loop: %s", err)

    async def shutdown(self) -> None:
        """Cancel all pending grace tasks upon application shutdown."""
        async with self._lock:
            for task in self._grace_tasks.values():
                if not task.done():
                    task.cancel()
            self._grace_tasks.clear()

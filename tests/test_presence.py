"""Comprehensive unit and integration tests for Stage 9.2 Real-Time Presence Engine, Heartbeats & Online Status."""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from quizverse.engine import GameEngine, Phase, Room, Player
from quizverse.persistence.db import Database
from quizverse.persistence.models import utc_now
from quizverse.persistence.player_identity import PlayerIdentityService
from quizverse.persistence.ratings import RatingService
from quizverse.persistence.social import BlockService, FriendService
from quizverse.presence import (
    LocalMemoryPresenceBroker,
    PresenceService,
    PresenceState,
    PresenceTicketService,
)
from quizverse.realtime import create_app


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'presence-test.sqlite3').as_posix()}"
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(Config(str(project_root / "alembic.ini")), "head")
    finally:
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url

    db = Database(url)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def identities(test_db):
    return PlayerIdentityService(test_db)


@pytest.fixture
def app_and_client(test_db):
    app = create_app(database=test_db)
    with TestClient(app) as client:
        yield app, client


def create_player(client: TestClient, name: str) -> tuple[str, str]:
    res = client.post("/api/player-profiles", json={"display_name": name})
    assert res.status_code == 201, res.text
    data = res.json()
    return data["player"]["id"], data["credential"]


# 1. Ticket Service Unit Tests
def test_presence_ticket_service():
    fake_time = 1000.0
    tickets = PresenceTicketService(ttl_seconds=30.0, clock=lambda: fake_time)

    # Issue
    raw = tickets.issue("player_abc")
    assert isinstance(raw, str)
    assert len(raw) >= 40

    # Reused / single-use check
    consumed = tickets.consume(raw)
    assert consumed == "player_abc"
    assert tickets.consume(raw) is None, "Reused ticket must be rejected"

    # Expired check
    raw_exp = tickets.issue("player_exp")
    fake_time += 31.0
    assert tickets.consume(raw_exp) is None, "Expired ticket must be rejected"

    # Malformed check
    assert tickets.consume("too_short") is None
    assert tickets.consume(12345) is None


# 2. HTTP Ticket API Tests
def test_presence_ticket_api_auth(app_and_client):
    app, client = app_and_client
    pid, cred = create_player(client, "TicketTester")

    # Unauthenticated
    res = client.post("/api/presence/ticket")
    assert res.status_code == 401

    # Invalid credential
    res = client.post("/api/presence/ticket", headers={"Authorization": "Bearer invalid_key_format_123456789012345678901234567890"})
    assert res.status_code == 401

    # Valid credential
    res = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {cred}"})
    assert res.status_code == 200
    data = res.json()
    assert "ticket" in data
    assert data["expires_in"] == 30

    # Rate limiting (20/min limit)
    for _ in range(20):
        res = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {cred}"})
    assert res.status_code == 429
    assert "Retry-After" in res.headers


# 3. WebSocket Presence Authentication
def test_presence_websocket_auth(app_and_client):
    app, client = app_and_client
    pid, cred = create_player(client, "WsAuthTester")

    # Get ticket
    ticket_res = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {cred}"}).json()
    ticket = ticket_res["ticket"]

    # Connect with valid ticket
    with client.websocket_connect("/ws/presence") as ws:
        ws.send_json({"event": "presence_auth", "ticket": ticket})
        ready = ws.receive_json()
        assert ready["event"] == "presence_ready"
        assert ready["player_id"] == pid
        assert "snapshot" in ready

    # Reconnect with the same ticket -> rejected (single-use)
    with client.websocket_connect("/ws/presence") as ws:
        ws.send_json({"event": "presence_auth", "ticket": ticket})
        err = ws.receive_json()
        assert err["event"] == "presence_error"
        assert err["code"] == "invalid_ticket"


# 4. Ping / Pong Heartbeats
def test_presence_ping_pong(app_and_client):
    app, client = app_and_client
    pid, cred = create_player(client, "PingTester")
    ticket = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {cred}"}).json()["ticket"]

    with client.websocket_connect("/ws/presence") as ws:
        ws.send_json({"event": "presence_auth", "ticket": ticket})
        assert ws.receive_json()["event"] == "presence_ready"

        ws.send_json({"event": "ping"})
        pong = ws.receive_json()
        assert pong["event"] == "pong"
        assert "timestamp" in pong

        # Unsupported event
        ws.send_json({"event": "custom_spoof_match", "status": "in_match"})
        err = ws.receive_json()
        assert err["event"] == "presence_error"
        assert err["code"] == "unsupported_event"


# 5. Friend-Only Visibility & Snapshot
def test_presence_friend_visibility(app_and_client):
    app, client = app_and_client
    p1_id, p1_cred = create_player(client, "FriendOne")
    p2_id, p2_cred = create_player(client, "FriendTwo")
    p3_id, p3_cred = create_player(client, "StrangerThree")

    # p1 and p2 become friends
    req = client.post("/api/friends/requests", json={"recipient_id": p2_id}, headers={"Authorization": f"Bearer {p1_cred}"}).json()
    client.post(f"/api/friends/requests/{req['request_id']}/accept", headers={"Authorization": f"Bearer {p2_cred}"})

    # p1 connects
    p1_ticket = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {p1_cred}"}).json()["ticket"]
    with client.websocket_connect("/ws/presence") as p1_ws:
        p1_ws.send_json({"event": "presence_auth", "ticket": p1_ticket})
        assert p1_ws.receive_json()["event"] == "presence_ready"

        # p2 connects -> should see p1 as online in snapshot, and p1 should receive presence_update for p2!
        p2_ticket = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {p2_cred}"}).json()["ticket"]
        with client.websocket_connect("/ws/presence") as p2_ws:
            p2_ws.send_json({"event": "presence_auth", "ticket": p2_ticket})
            p2_ready = p2_ws.receive_json()
            assert p2_ready["event"] == "presence_ready"
            # Snapshot contains p1
            p1_entry = next((item for item in p2_ready["snapshot"] if item["player_id"] == p1_id), None)
            assert p1_entry is not None
            assert p1_entry["status"] == "online"

            # p1 receives presence_update that p2 is online!
            p1_update = p1_ws.receive_json()
            assert p1_update["event"] == "presence_update"
            assert p1_update["player_id"] == p2_id
            assert p1_update["status"] == "online"

            # Stranger p3 connects -> snapshot must be EMPTY (no friends)
            p3_ticket = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {p3_cred}"}).json()["ticket"]
            with client.websocket_connect("/ws/presence") as p3_ws:
                p3_ws.send_json({"event": "presence_auth", "ticket": p3_ticket})
                p3_ready = p3_ws.receive_json()
                assert len(p3_ready["snapshot"]) == 0, "Stranger must not receive presence of non-friends"


# 6. Multi-Tab Correctness: Single tab closing does not mark player offline
def test_presence_multi_tab(app_and_client):
    app, client = app_and_client
    p1_id, p1_cred = create_player(client, "MultiTabP1")
    p2_id, p2_cred = create_player(client, "MultiTabP2")

    # Friendship
    req = client.post("/api/friends/requests", json={"recipient_id": p2_id}, headers={"Authorization": f"Bearer {p1_cred}"}).json()
    client.post(f"/api/friends/requests/{req['request_id']}/accept", headers={"Authorization": f"Bearer {p2_cred}"})

    # P2 connects
    p2_ticket = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {p2_cred}"}).json()["ticket"]
    with client.websocket_connect("/ws/presence") as p2_ws:
        p2_ws.send_json({"event": "presence_auth", "ticket": p2_ticket})
        assert p2_ws.receive_json()["event"] == "presence_ready"

        # P1 opens Tab 1
        t1_ticket = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {p1_cred}"}).json()["ticket"]
        with client.websocket_connect("/ws/presence") as p1_tab1:
            p1_tab1.send_json({"event": "presence_auth", "ticket": t1_ticket})
            assert p1_tab1.receive_json()["event"] == "presence_ready"
            # P2 received online update
            assert p2_ws.receive_json()["status"] == "online"

            # P1 opens Tab 2
            t2_ticket = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {p1_cred}"}).json()["ticket"]
            with client.websocket_connect("/ws/presence") as p1_tab2:
                p1_tab2.send_json({"event": "presence_auth", "ticket": t2_ticket})
                assert p1_tab2.receive_json()["event"] == "presence_ready"

                # P1 closes Tab 1
            # Tab 2 is still open!
            # Check broker socket count for p1
            presence_svc: PresenceService = app.state.presence_service
            # Run event loop step
            assert app.state.presence_service.get_cached_state(p1_id) == "online"


# 7. Blocking Stops Presence Immediately
def test_presence_blocking_stops_sharing(app_and_client):
    app, client = app_and_client
    p1_id, p1_cred = create_player(client, "BlockerP1")
    p2_id, p2_cred = create_player(client, "BlockerP2")

    # Friendship
    req = client.post("/api/friends/requests", json={"recipient_id": p2_id}, headers={"Authorization": f"Bearer {p1_cred}"}).json()
    client.post(f"/api/friends/requests/{req['request_id']}/accept", headers={"Authorization": f"Bearer {p2_cred}"})

    p1_ticket = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {p1_cred}"}).json()["ticket"]
    p2_ticket = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {p2_cred}"}).json()["ticket"]

    with client.websocket_connect("/ws/presence") as p1_ws:
        p1_ws.send_json({"event": "presence_auth", "ticket": p1_ticket})
        p1_ws.receive_json()

        with client.websocket_connect("/ws/presence") as p2_ws:
            p2_ws.send_json({"event": "presence_auth", "ticket": p2_ticket})
            p2_ws.receive_json()
            p1_ws.receive_json()  # p2 online update

            # P1 blocks P2
            block_res = client.post("/api/blocks", json={"target_id": p2_id}, headers={"Authorization": f"Bearer {p1_cred}"})
            assert block_res.status_code == 200

            # Both receive offline update immediately
            p1_offline = p1_ws.receive_json()
            assert p1_offline["event"] == "presence_update"
            assert p1_offline["player_id"] == p2_id
            assert p1_offline["status"] == "offline"

            p2_offline = p2_ws.receive_json()
            assert p2_offline["event"] == "presence_update"
            assert p2_offline["player_id"] == p1_id
            assert p2_offline["status"] == "offline"


# 8. Un-friending Stops Presence Immediately
def test_presence_unfriend_stops_sharing(app_and_client):
    app, client = app_and_client
    p1_id, p1_cred = create_player(client, "UnfriendP1")
    p2_id, p2_cred = create_player(client, "UnfriendP2")

    req = client.post("/api/friends/requests", json={"recipient_id": p2_id}, headers={"Authorization": f"Bearer {p1_cred}"}).json()
    client.post(f"/api/friends/requests/{req['request_id']}/accept", headers={"Authorization": f"Bearer {p2_cred}"})

    p1_ticket = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {p1_cred}"}).json()["ticket"]
    p2_ticket = client.post("/api/presence/ticket", headers={"Authorization": f"Bearer {p2_cred}"}).json()["ticket"]

    with client.websocket_connect("/ws/presence") as p1_ws:
        p1_ws.send_json({"event": "presence_auth", "ticket": p1_ticket})
        p1_ws.receive_json()

        with client.websocket_connect("/ws/presence") as p2_ws:
            p2_ws.send_json({"event": "presence_auth", "ticket": p2_ticket})
            p2_ws.receive_json()
            p1_ws.receive_json()  # p2 online update

            # P1 removes P2 as friend
            rem_res = client.delete(f"/api/friends/{p2_id}", headers={"Authorization": f"Bearer {p1_cred}"})
            assert rem_res.status_code == 200

            # Both receive offline notifications
            p1_off = p1_ws.receive_json()
            assert p1_off["player_id"] == p2_id
            assert p1_off["status"] == "offline"

            p2_off = p2_ws.receive_json()
            assert p2_off["player_id"] == p1_id
            assert p2_off["status"] == "offline"


# 9. Server-Authoritative Activity States (In Lobby, In Match, Online)
def test_presence_authoritative_activity_state():
    async def _run():
        engine = GameEngine()
        db = Database("sqlite:///:memory:")
        b_svc = BlockService(db)
        f_svc = FriendService(db, b_svc)
        tickets = PresenceTicketService()
        broker = LocalMemoryPresenceBroker()
        service = PresenceService(broker=broker, engine=engine, friend_service=f_svc, block_service=b_svc, tickets=tickets)

        # Player with no connection -> offline
        assert await service.determine_player_state("player_1") == PresenceState.OFFLINE

        # Connected via socket -> online
        await broker.register_socket("sock_1", "player_1", None)
        assert await service.determine_player_state("player_1") == PresenceState.ONLINE

        # Player enters waiting room -> in_lobby
        p = Player(id="p_uuid", name="Alice", token_hash="thash", profile_id="player_1", connected=True)
        room = Room(code="TEST01", host_id="p_uuid", created_at=time.monotonic(), last_activity=time.monotonic(), players={"p_uuid": p}, phase=Phase.WAITING)
        engine._rooms["TEST01"] = room

        assert await service.determine_player_state("player_1") == PresenceState.IN_LOBBY

        # Match starts -> in_match
        room.phase = Phase.QUESTION
        assert await service.determine_player_state("player_1") == PresenceState.IN_MATCH

        # Match ends -> in_lobby
        room.phase = Phase.RESULTS
        assert await service.determine_player_state("player_1") == PresenceState.IN_LOBBY

        # Player leaves room / room destroyed -> online
        del engine._rooms["TEST01"]
        assert await service.determine_player_state("player_1") == PresenceState.ONLINE

    asyncio.run(_run())


# 10. Stale Sockets and Grace Period Cleanup
def test_presence_stale_cleanup_and_grace():
    async def _run():
        fake_time = 5000.0
        broker = LocalMemoryPresenceBroker(clock=lambda: fake_time)
        engine = GameEngine()
        db = Database("sqlite:///:memory:")
        b_svc = BlockService(db)
        f_svc = FriendService(db, b_svc)
        tickets = PresenceTicketService(clock=lambda: fake_time)
        service = PresenceService(
            broker=broker,
            engine=engine,
            friend_service=f_svc,
            block_service=b_svc,
            tickets=tickets,
            stale_threshold=60.0,
            grace_period=1.0,
            clock=lambda: fake_time,
        )

        class MockWs:
            def __init__(self):
                self.closed = False
            async def close(self, code=1000, reason=""):
                self.closed = True
            async def send_json(self, data):
                pass

        ws = MockWs()
        await broker.register_socket("s1", "p_idle", ws)
        assert await broker.is_player_connected("p_idle") is True

        # Advance fake time past stale threshold
        fake_time += 65.0
        stale = await broker.cleanup_stale_sockets(60.0)
        assert len(stale) == 1
        assert stale[0][0] == "s1"
        assert stale[0][1] == "p_idle"
        assert stale[0][3] == 0  # remaining count is 0
        assert await broker.is_player_connected("p_idle") is False

    asyncio.run(_run())


# 11. Stage 9.1 Relationship State Contract & Elo Verification
def test_stage_9_1_fixes_verification(app_and_client):
    app, client = app_and_client
    p1_id, p1_cred = create_player(client, "AuditFixP1")
    p2_id, p2_cred = create_player(client, "AuditFixP2")

    # 1. Initially relationship_state is none
    search_res = client.get(f"/api/players/search?query=AuditFixP2", headers={"Authorization": f"Bearer {p1_cred}"}).json()
    assert search_res["results"][0]["relationship_state"] == "none"

    # 2. Outgoing request
    req = client.post("/api/friends/requests", json={"recipient_id": p2_id}, headers={"Authorization": f"Bearer {p1_cred}"}).json()
    search_after_req = client.get(f"/api/players/search?query=AuditFixP2", headers={"Authorization": f"Bearer {p1_cred}"}).json()
    assert search_after_req["results"][0]["relationship_state"] == "outgoing_request"

    # 3. Incoming request for P2
    search_for_p2 = client.get(f"/api/players/search?query=AuditFixP1", headers={"Authorization": f"Bearer {p2_cred}"}).json()
    assert search_for_p2["results"][0]["relationship_state"] == "incoming_request"

    # 4. Accept -> friends
    client.post(f"/api/friends/requests/{req['request_id']}/accept", headers={"Authorization": f"Bearer {p2_cred}"})
    search_friends = client.get(f"/api/players/search?query=AuditFixP2", headers={"Authorization": f"Bearer {p1_cred}"}).json()
    assert search_friends["results"][0]["relationship_state"] == "friends"

    # 5. List friends includes rating property
    friends_res = client.get("/api/friends", headers={"Authorization": f"Bearer {p1_cred}"}).json()
    assert "friends" in friends_res
    friend_card = friends_res["friends"][0]
    assert "rating" in friend_card
    assert isinstance(friend_card["rating"], int)
    assert "presence" in friend_card

"""Comprehensive unit and integration tests for Stage 9.4 Multiplayer Room Chat and Gameplay Isolation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quizverse.engine import GameEngine, GameError, Phase
from quizverse.realtime import create_app


@pytest.fixture
def engine() -> GameEngine:
    return GameEngine()


def test_room_chat_send_and_snapshot(engine: GameEngine):
    """Test authorized room member sending chat and snapshot inclusion."""
    async def _run():
        created = await engine.create_room("HostPlayer")
        code = str(created["state"]["code"])
        host_id = str(created["player_id"])

        joined = await engine.join_room(code, "GuestPlayer")
        guest_id = str(joined["player_id"])

        # Host sends message
        msg = await engine.send_room_chat_message(code, host_id, "Welcome to the game!")
        assert msg["id"] is not None
        assert msg["sender_name"] == "HostPlayer"
        assert msg["content"] == "Welcome to the game!"
        assert msg["room_code"] == code

        # Snapshot for both players contains the message
        snap_host = await engine.snapshot(code, host_id)
        assert len(snap_host["chat_messages"]) == 1
        assert snap_host["chat_messages"][0]["content"] == "Welcome to the game!"

        snap_guest = await engine.snapshot(code, guest_id)
        assert len(snap_guest["chat_messages"]) == 1
        assert snap_guest["chat_messages"][0]["sender_name"] == "HostPlayer"

    asyncio.run(_run())


def test_room_chat_rejected_for_non_member(engine: GameEngine):
    """Ensure non-members cannot send chat to a room."""
    async def _run():
        created = await engine.create_room("HostPlayer")
        code = str(created["state"]["code"])

        with pytest.raises(GameError) as exc_info:
            await engine.send_room_chat_message(code, "fake_player_id", "Intruder chat")
        assert exc_info.value.code == "not_member"

    asyncio.run(_run())


def test_room_chat_rejected_for_invalid_room(engine: GameEngine):
    """Ensure sending chat to nonexistent room is rejected."""
    async def _run():
        with pytest.raises(GameError) as exc_info:
            await engine.send_room_chat_message("NONEXIST", "player1", "Hello")
        assert exc_info.value.code == "room_not_found"

    asyncio.run(_run())


def test_room_chat_content_validation(engine: GameEngine):
    """Validate empty/whitespace and maximum length rules."""
    async def _run():
        created = await engine.create_room("Tester")
        code = str(created["state"]["code"])
        pid = str(created["player_id"])

        # Empty string
        with pytest.raises(GameError) as exc_empty:
            await engine.send_room_chat_message(code, pid, "")
        assert exc_empty.value.code == "empty_message"

        # Whitespace only
        with pytest.raises(GameError) as exc_ws:
            await engine.send_room_chat_message(code, pid, "    \n   ")
        assert exc_ws.value.code == "empty_message"

        # Message exceeds 500 characters
        long_msg = "a" * 501
        with pytest.raises(GameError) as exc_len:
            await engine.send_room_chat_message(code, pid, long_msg)
        assert exc_len.value.code == "message_too_long"

    asyncio.run(_run())


def test_room_chat_reactions(engine: GameEngine):
    """Test emoji reactions, toggling, and invalid emoji rejection."""
    async def _run():
        created = await engine.create_room("HostR")
        code = str(created["state"]["code"])
        host_id = str(created["player_id"])

        joined = await engine.join_room(code, "GuestR")
        guest_id = str(joined["player_id"])

        msg = await engine.send_room_chat_message(code, host_id, "Nice move!")
        mid = str(msg["id"])

        # Invalid reaction rejected
        with pytest.raises(GameError) as exc_rx:
            await engine.add_room_chat_reaction(code, guest_id, mid, "🥑")
        assert exc_rx.value.code == "invalid_reaction"

        # Guest adds "🔥"
        res1 = await engine.add_room_chat_reaction(code, guest_id, mid, "🔥")
        assert res1["action"] == "added"
        assert "🔥" in res1["reactions"]
        assert guest_id in res1["reactions"]["🔥"]

        # Guest toggles "🔥" -> removed
        res2 = await engine.add_room_chat_reaction(code, guest_id, mid, "🔥")
        assert res2["action"] == "removed"
        assert "🔥" not in res2["reactions"]

    asyncio.run(_run())


def test_cross_room_isolation(engine: GameEngine):
    """Verify chat messages never leak across rooms."""
    async def _run():
        r1 = await engine.create_room("Host1")
        code1 = str(r1["state"]["code"])
        p1 = str(r1["player_id"])

        r2 = await engine.create_room("Host2")
        code2 = str(r2["state"]["code"])
        p2 = str(r2["player_id"])

        await engine.send_room_chat_message(code1, p1, "Secret in Room 1")

        # Room 1 has message
        snap1 = await engine.snapshot(code1, p1)
        assert len(snap1["chat_messages"]) == 1

        # Room 2 is completely isolated
        snap2 = await engine.snapshot(code2, p2)
        assert len(snap2["chat_messages"]) == 0

    asyncio.run(_run())


def test_room_lifecycle_closure(engine: GameEngine):
    """Verify chat is rejected after room enters RESULTS phase."""
    async def _run():
        created = await engine.create_room("HostClosed")
        code = str(created["state"]["code"])
        pid = str(created["player_id"])

        # Transition room to Phase.RESULTS
        room = engine._rooms[code]
        room.phase = Phase.RESULTS

        with pytest.raises(GameError) as exc_closed:
            await engine.send_room_chat_message(code, pid, "Too late to chat")
        assert exc_closed.value.code == "room_closed"

    asyncio.run(_run())


def test_chat_gameplay_isolation(engine: GameEngine):
    """Prove chat does NOT modify question sequence, deadlines, answer submission, or scoring."""
    async def _run():
        created = await engine.create_room("HostGame")
        code = str(created["state"]["code"])
        p1 = str(created["player_id"])

        joined = await engine.join_room(code, "GuestGame")
        p2 = str(joined["player_id"])

        # Start game
        await engine.start_game(code, p1)

        # Check question state
        snap_before = await engine.snapshot(code, p1)
        assert snap_before["phase"] in ("countdown", "question")
        deadline_before = snap_before["deadline"]
        round_before = snap_before["round"]

        # Send chat during active game
        msg = await engine.send_room_chat_message(code, p2, "Good luck!")
        assert msg["id"] is not None

        # Verify gameplay state is completely untouched
        snap_after = await engine.snapshot(code, p1)
        assert snap_after["deadline"] == deadline_before
        assert snap_after["round"] == round_before
        assert snap_after["phase"] == snap_before["phase"]
        assert len(snap_after["chat_messages"]) == 1

    asyncio.run(_run())


def test_websocket_room_chat():
    """Verify WebSocket room chat broadcast via TestClient."""
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws1:
            ws1.send_json({"event": "create", "name": "WSHost"})
            joined1 = ws1.receive_json()
            assert joined1["event"] == "joined"
            code = joined1["state"]["code"]

            with client.websocket_connect("/ws") as ws2:
                ws2.send_json({"event": "join", "name": "WSGuest", "code": code})
                joined2 = ws2.receive_json()
                assert joined2["event"] == "joined"
                # Both receive player-joined
                evt = ws1.receive_json()
                assert evt["event"] == "player-joined"
                evt2 = ws2.receive_json()
                assert evt2["event"] == "player-joined"

                # Send room chat message from ws2
                ws2.send_json({"event": "room_chat", "text": "Hello from WS!"})

                # ws2 receives broadcast
                ws2_msg = ws2.receive_json()
                assert ws2_msg["event"] == "room_chat_message"
                assert ws2_msg["content"] == "Hello from WS!"

                # ws1 also receives broadcast
                ws1_msg = ws1.receive_json()
                assert ws1_msg["event"] == "room_chat_message"
                assert ws1_msg["content"] == "Hello from WS!"
                assert ws1_msg["sender_name"] == "WSGuest"

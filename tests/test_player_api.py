from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from quizverse.persistence.db import Database
from quizverse.engine import GameEngine, Phase
from quizverse.realtime import create_app


@pytest.fixture
def player_client(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'player-api.sqlite3').as_posix()}"
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(Config(str(project_root / "alembic.ini")), "head")
    finally:
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url
    database = Database(url)
    try:
        with TestClient(create_app(database=database)) as client:
            yield client, database
    finally:
        database.close()


def create_profile(client: TestClient, name: str = "Player One") -> tuple[dict, str]:
    response = client.post("/api/player-profiles", json={"display_name": name})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    return body["player"], body["credential"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_profile_api_creation_auth_update_rotation_and_deletion(player_client):
    client, _ = player_client
    player, token = create_profile(client)
    assert player["display_name"] == "Player One" and player["status"] == "active"
    assert "token_hash" not in player and "credential" not in player
    assert client.get("/api/player-profiles/me", headers=auth(token)).json()["id"] == player["id"]
    assert client.get("/api/player-profiles/me").status_code == 401

    updated = client.patch(
        "/api/player-profiles/me", headers=auth(token),
        json={"display_name": "Nova", "expected_version": player["version"]},
    )
    assert updated.status_code == 200 and updated.json()["display_name"] == "Nova"
    conflict = client.patch(
        "/api/player-profiles/me", headers=auth(token),
        json={"display_name": "Stale", "expected_version": player["version"]},
    )
    assert conflict.status_code == 409

    rotated = client.post("/api/player-profiles/me/credentials/rotate", headers=auth(token))
    assert rotated.status_code == 200 and rotated.headers["cache-control"] == "no-store"
    new_token = rotated.json()["credential"]
    assert new_token != token
    assert client.get("/api/player-profiles/me", headers=auth(token)).status_code == 401
    assert client.get("/api/player-profiles/me", headers=auth(new_token)).status_code == 200

    deleted = client.delete("/api/player-profiles/me", headers=auth(new_token))
    assert deleted.status_code == 204
    assert client.get("/api/player-profiles/me", headers=auth(new_token)).status_code == 401


def test_profile_api_validation_privacy_stats_and_no_result_write_route(player_client):
    client, _ = player_client
    player, token = create_profile(client, "Same nickname")
    _, another_token = create_profile(client, "Same nickname")
    assert player["id"] != client.get("/api/player-profiles/me", headers=auth(another_token)).json()["id"]
    invalid = client.post("/api/player-profiles", json={"display_name": "\nBad", "player_id": "forged"})
    assert invalid.status_code == 422
    assert "forged" not in invalid.text
    stats = client.get("/api/player-profiles/me/competitive-stats", headers=auth(token)).json()
    assert stats["history_available"] is False and stats["wins"] == 0
    matches = client.get("/api/player-profiles/me/matches", headers=auth(token)).json()
    assert matches["history_available"] is False and matches["matches"] == []
    assert client.post("/api/player-profiles/me/matches", headers=auth(token), json={"score": 999999}).status_code == 405
    assert client.post("/api/matches/result", json={"score": 999999}).status_code not in {200, 201, 204}


def test_single_use_profile_ticket_binds_server_side_and_never_enters_snapshots(player_client):
    client, _ = player_client
    profile, token = create_profile(client)
    issued = client.post("/api/player-profiles/me/live-ticket", headers=auth(token))
    assert issued.status_code == 200
    ticket = issued.json()["live_ticket"]
    assert len(ticket) >= 40

    with client.websocket_connect("/ws") as host:
        host.send_json({"event": "create", "name": "Host", "identity_ticket": ticket})
        joined = host.receive_json()
        assert joined["event"] == "joined"
        assert "identity_ticket" not in joined and "profile_id" not in joined["state"]
        room = client.app.state.hub.engine._rooms[joined["state"]["code"]]
        assert room.players[joined["player_id"]].profile_id == profile["id"]

        repeat = client.post("/api/player-profiles/me/live-ticket", headers=auth(token)).json()["live_ticket"]
        with client.websocket_connect("/ws") as duplicate:
            duplicate.send_json({"event": "join", "name": "Another seat", "code": joined["state"]["code"], "identity_ticket": repeat})
            rejected = duplicate.receive_json()
            assert rejected["event"] == "error" and rejected["code"] == "profile_already_in_room"


def test_websocket_tickets_are_optional_and_single_use(player_client):
    client, _ = player_client
    profile, token = create_profile(client)
    ticket = client.post("/api/player-profiles/me/live-ticket", headers=auth(token)).json()["live_ticket"]
    with client.websocket_connect("/ws") as host:
        host.send_json({"event": "create", "name": "Host", "identity_ticket": ticket})
        joined = host.receive_json()
        assert joined["event"] == "joined"
        room_code = joined["state"]["code"]
        with client.websocket_connect("/ws") as casual:
            casual.send_json({"event": "join", "name": "Guest", "code": room_code})
            assert casual.receive_json()["event"] == "joined"
            assert all("profile_id" not in row for row in joined["state"]["players"])
        with client.websocket_connect("/ws") as reused:
            reused.send_json({"event": "create", "name": "Reuse", "identity_ticket": ticket})
            error = reused.receive_json()
            assert error["event"] == "error" and error["code"] == "invalid_identity_ticket"


def test_terminal_engine_result_is_persisted_and_appears_in_profile_history(player_client):
    client, _ = player_client
    profile, token = create_profile(client, "Match history")
    engine = client.app.state.hub.engine

    class Clock:
        def __init__(self):
            self.monotonic_now = 100.0
            self.wall_now = 1_800_000_000.0

        def monotonic(self):
            return self.monotonic_now

        def wall(self):
            return self.wall_now

        def advance(self, value):
            self.monotonic_now += value
            self.wall_now += value

    clock = Clock()
    engine._monotonic = clock.monotonic
    engine._wall_time = clock.wall

    async def drive():
        joined = await engine.create_room("Practice", "solo_practice", profile_id=profile["id"])
        code = joined["state"]["code"]
        await engine.start_game(code, joined["player_id"])
        for _ in range(100):
            state = await engine.snapshot(code, joined["player_id"])
            if state["phase"] == Phase.RESULTS:
                break
            room = engine._rooms[code]
            assert room.deadline is not None
            clock.advance(max(0.001, room.deadline - clock.monotonic()))
            await engine.advance_due()
        assert (await engine.snapshot(code, joined["player_id"]))["phase"] == Phase.RESULTS
        await client.app.state.hub._persist_completed_matches()

    asyncio.run(drive())

    matches = client.get("/api/player-profiles/me/matches", headers=auth(token)).json()
    assert matches["history_available"] and matches["total"] == 1
    match = matches["matches"][0]
    assert match["mode"] == "solo_practice" and match["result_kind"] == "practice"
    assert match["outcome"] == "practice" and match["placement"] is None
    assert "question_set_hash" not in match and "correct_answer" not in match
    stats = client.get("/api/player-profiles/me/competitive-stats", headers=auth(token)).json()
    assert stats["completed_matches"] == 1 and stats["wins"] == stats["losses"] == stats["draws"] == 0

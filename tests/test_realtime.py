import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import websockets
from fastapi.testclient import TestClient
from websockets.exceptions import ConnectionClosedError, InvalidStatus

from quizverse.engine import COUNTDOWN_SECONDS, GameEngine, QUESTION_SECONDS, Phase
from quizverse.realtime import create_app
from quizverse.questions import QUESTIONS
from quizverse.special_questions import ESCAPE_QUESTIONS

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def server_url() -> str:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    launcher_name = "quizverse-live.exe" if os.name == "nt" else "quizverse-live"
    launcher = Path(sys.executable).parent / launcher_name
    process = subprocess.Popen(
        [str(launcher)],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "ALLOWED_ORIGINS": f"http://127.0.0.1:{port},http://localhost:{port}",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Uvicorn exited with code {process.returncode}")
        try:
            if httpx.get(f"{base_url}/health", timeout=0.3).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        process.terminate()
        raise RuntimeError("Uvicorn did not start before the test timeout")
    yield base_url
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def receive_until(socket: websockets.ClientConnection, predicate, timeout: float = 5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = await asyncio.wait_for(socket.recv(), timeout=max(0.05, deadline - time.monotonic()))
        import json

        parsed = json.loads(message)
        if predicate(parsed):
            return parsed
    raise AssertionError("Expected WebSocket event did not arrive")


async def open_client(url: str) -> websockets.ClientConnection:
    return await websockets.connect(url.replace("http://", "ws://") + "/ws", origin=None, max_size=4096)


def test_http_health_and_python_frontend_are_served(server_url: str) -> None:
    health = httpx.get(f"{server_url}/health")
    page = httpx.get(server_url)
    assert health.json() == {
        "ok": True,
        "service": "quizverse-live",
        "pending_match_history": 0,
        "retrying_match_history": 0,
    }
    assert health.headers["content-security-policy"].startswith("default-src 'self'")
    assert health.headers["x-content-type-options"] == "nosniff"
    assert page.status_code == 200
    script = httpx.get(f"{server_url}/app.js")
    assert "Create Arena" in script.text
    assert "Join with Code" in script.text


def test_two_websocket_players_join_sync_answer_and_reconnect(server_url: str) -> None:
    async def exercise() -> None:
        first = await open_client(server_url)
        second = await open_client(server_url)
        try:
            await first.send('{"event":"create","name":"Nova"}')
            host = await receive_until(first, lambda item: item.get("event") == "joined")
            code = host["state"]["code"]
            assert len(code) == 6
            assert host["state"]["mode"] == "classic"

            await second.send('{"event":"join","name":"Orbit","code":"XXXXXX"}')
            invalid = await receive_until(second, lambda item: item.get("event") == "error")
            assert invalid["code"] in {"invalid_code", "room_not_found"}
            await second.send(f'{{"event":"join","name":"Orbit","code":"{code}"}}')
            guest = await receive_until(second, lambda item: item.get("event") == "joined")
            assert len(guest["state"]["players"]) == 2

            await second.send('{"event":"start"}')
            denied = await receive_until(second, lambda item: item.get("event") == "error")
            assert denied["code"] == "host_only"
            await first.send('{"event":"start"}')
            question_host = await receive_until(first, lambda item: item.get("state", {}).get("phase") == "question")
            question_guest = await receive_until(second, lambda item: item.get("state", {}).get("phase") == "question")
            assert question_host["state"]["deadline"] == question_guest["state"]["deadline"]
            assert question_host["state"]["question_seconds"] == 15.0
            remaining_ms = question_host["state"]["deadline"] - question_host["state"]["server_now"]
            assert 0 < remaining_ms <= 15000
            assert "answer" not in question_host["state"]["question"]

            # The answer option index is server-validated. Both responses serialize into
            # the reveal transition; the correct option is absent from the live state.
            await first.send('{"event":"answer","choice":1}')
            locked = await receive_until(
                first,
                lambda item: item.get("event") == "answer-update" and item.get("state", {}).get("answered"),
            )
            assert locked["state"]["phase"] == "question"
            await first.send('{"event":"answer","choice":1}')
            duplicate = await receive_until(first, lambda item: item.get("event") == "error")
            assert duplicate["code"] == "duplicate_answer"
            await second.send('{"event":"answer","choice":2}')
            reveal_host = await receive_until(first, lambda item: item.get("state", {}).get("phase") == "reveal", timeout=5)
            reveal_guest = await receive_until(second, lambda item: item.get("state", {}).get("phase") == "reveal", timeout=5)
            assert reveal_host["state"]["answer_feedback"]["correct"] == reveal_guest["state"]["answer_feedback"]["correct"]
            assert "answer" not in reveal_host["state"]["question"]

            await first.send('{"event":"answer","choice":0}')
            stale = await receive_until(first, lambda item: item.get("event") == "error")
            assert stale["code"] == "stale_answer"

            await second.close()
            resumed = await open_client(server_url)
            try:
                await resumed.send('{"event":"reconnect","token":"' + guest["token"] + '"}')
                recovered = await receive_until(resumed, lambda item: item.get("event") == "joined")
                assert recovered["player_id"] == guest["player_id"]
                assert any(player["connected"] for player in recovered["state"]["players"] if player["id"] == guest["player_id"])
            finally:
                await resumed.close()
        finally:
            await first.close()
            if second.state.name == "OPEN":
                await second.close()

    asyncio.run(exercise())


def test_speed_challenge_mode_and_deadline_are_authoritative_over_websocket(server_url: str) -> None:
    async def exercise() -> None:
        host_socket = await open_client(server_url)
        guest_socket = await open_client(server_url)
        try:
            await host_socket.send('{"event":"create","name":"Nova","mode":"speed_challenge"}')
            host = await receive_until(host_socket, lambda item: item.get("event") == "joined")
            code = host["state"]["code"]
            await guest_socket.send('{"event":"join","name":"Orbit","code":"' + code + '"}')
            await receive_until(guest_socket, lambda item: item.get("event") == "joined")
            await host_socket.send('{"event":"start"}')
            host_question = await receive_until(
                host_socket, lambda item: item.get("state", {}).get("phase") == "question"
            )
            guest_question = await receive_until(
                guest_socket, lambda item: item.get("state", {}).get("phase") == "question"
            )
            host_state = host_question["state"]
            guest_state = guest_question["state"]
            assert host_state["mode"] == guest_state["mode"] == "speed_challenge"
            assert host_state["question_seconds"] == guest_state["question_seconds"] == 10.0
            assert host_state["deadline"] == guest_state["deadline"]
            remaining_ms = host_state["deadline"] - host_state["server_now"]
            assert 0 < remaining_ms <= 10000
            assert host_state["momentum"]["enabled"] is False
            assert "answer" not in host_state["question"]
        finally:
            await host_socket.close()
            await guest_socket.close()

    asyncio.run(exercise())


def test_team_battle_assignments_and_deadlines_are_synchronized_for_four_clients(server_url: str) -> None:
    async def exercise() -> None:
        clients = [await open_client(server_url) for _ in range(4)]
        try:
            await clients[0].send('{"event":"create","name":"Violet Host","mode":"team_battle"}')
            host = await receive_until(clients[0], lambda item: item.get("event") == "joined")
            code = host["state"]["code"]
            player_ids = [host["player_id"]]
            assert host["state"]["can_start"] is False
            for index, client in enumerate(clients[1:], start=1):
                name = f"Player {index}"
                await client.send('{"event":"join","name":"' + name + '","code":"' + code + '"}')
                joined = await receive_until(client, lambda item: item.get("event") == "joined")
                player_ids.append(joined["player_id"])

            lobby_states = [
                await receive_until(
                    client,
                    lambda item: len(item.get("state", {}).get("players", [])) == 4,
                )
                for client in clients
            ]
            expected_teams = ["violet", "cyan", "violet", "cyan"]
            for client_index, lobby in enumerate(lobby_states):
                state = lobby["state"]
                assert state["mode"] == "team_battle"
                assert state["can_start"] is (player_ids[client_index] == host["player_id"])
                players_by_name = {player["name"]: player["team_id"] for player in state["players"]}
                assert [players_by_name["Violet Host"], players_by_name["Player 1"], players_by_name["Player 2"], players_by_name["Player 3"]] == expected_teams
                assert {team["team_id"] for team in state["teams"]} == {"violet", "cyan"}

            await clients[0].send('{"event":"start"}')
            question_states = [
                await receive_until(client, lambda item: item.get("state", {}).get("phase") == "question")
                for client in clients
            ]
            reference = question_states[0]["state"]
            for question in question_states[1:]:
                state = question["state"]
                assert state["deadline"] == reference["deadline"]
                assert state["teams"] == reference["teams"]
                assert {player["id"]: player["team_id"] for player in state["players"]} == {
                    player["id"]: player["team_id"] for player in reference["players"]
                }
            assert 0 < reference["deadline"] - reference["server_now"] <= 15000
            assert reference["momentum"]["enabled"] is False
        finally:
            await asyncio.gather(*(client.close() for client in clients))

    asyncio.run(exercise())


def test_solo_practice_websocket_starts_one_player_and_reveals_answer(server_url: str) -> None:
    async def exercise() -> None:
        client = await open_client(server_url)
        try:
            await client.send('{"event":"create","name":"Solo Nova","mode":"solo_practice"}')
            joined = await receive_until(client, lambda item: item.get("event") == "joined")
            assert joined["state"]["mode"] == "solo_practice"
            assert joined["state"]["can_start"] is True
            assert len(joined["state"]["players"]) == 1

            await client.send('{"event":"start"}')
            question = await receive_until(client, lambda item: item.get("state", {}).get("phase") == "question")
            state = question["state"]
            assert state["question_seconds"] == 15.0
            assert state["momentum"]["enabled"] is False
            assert "answer" not in state["question"]
            correct = next(item.answer for item in QUESTIONS if item.prompt == state["question"]["prompt"])
            await client.send('{"event":"answer","choice":' + str(correct) + '}')
            reveal = await receive_until(
                client,
                lambda item: item.get("event") == "answer-update" and item.get("state", {}).get("phase") == "reveal",
            )
            assert reveal["state"]["answer_feedback"]["was_correct"] is True
            assert reveal["state"]["players"][0]["score"] >= 1000
            assert len(reveal["state"]["players"]) == 1
        finally:
            await client.close()

    asyncio.run(exercise())


def test_late_websocket_answer_broadcasts_deadline_reveal_to_room() -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 100.0
            self.wall = 1_800_000_000.0

        def monotonic(self) -> float:
            return self.now

        def wall_time(self) -> float:
            return self.wall

        def advance(self, seconds: float) -> None:
            self.now += seconds
            self.wall += seconds

    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall_time)
    host = asyncio.run(engine.create_room("Nova"))
    guest = asyncio.run(engine.join_room(host["state"]["code"], "Orbit"))
    code = host["state"]["code"]
    asyncio.run(engine.start_game(code, host["player_id"]))
    clock.advance(COUNTDOWN_SECONDS)
    asyncio.run(engine.advance_due())
    assert asyncio.run(engine.snapshot(code, host["player_id"]))["phase"] == Phase.QUESTION
    clock.advance(QUESTION_SECONDS)

    app = create_app(engine=engine)

    async def pause_scheduler(stop: asyncio.Event) -> None:
        # The test advances the injected clock and sends the late event itself.
        await stop.wait()

    app.state.hub.scheduler = pause_scheduler

    def receive_matching(socket, predicate):
        for _ in range(12):
            message = socket.receive_json()
            if predicate(message):
                return message
        raise AssertionError("Expected WebSocket event was not received")

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as host_socket, client.websocket_connect("/ws") as guest_socket:
            host_socket.send_json({"event": "reconnect", "token": host["token"]})
            assert receive_matching(host_socket, lambda message: message.get("event") == "joined")["player_id"] == host["player_id"]
            guest_socket.send_json({"event": "reconnect", "token": guest["token"]})
            assert receive_matching(guest_socket, lambda message: message.get("event") == "joined")["player_id"] == guest["player_id"]

            receive_matching(host_socket, lambda message: message.get("event") == "player-reconnected")
            receive_matching(host_socket, lambda message: message.get("event") == "player-reconnected")
            receive_matching(guest_socket, lambda message: message.get("event") == "player-reconnected")

            guest_socket.send_json({"event": "answer", "choice": 0})
            error = receive_matching(guest_socket, lambda message: message.get("event") == "error")
            assert error["code"] == "stale_answer"
            host_reveal = receive_matching(
                host_socket,
                lambda message: message.get("event") == "state"
                and message.get("state", {}).get("phase") == Phase.REVEAL,
            )
            guest_reveal = receive_matching(
                guest_socket,
                lambda message: message.get("event") == "state"
                and message.get("state", {}).get("phase") == Phase.REVEAL,
            )
            for reveal in (host_reveal["state"], guest_reveal["state"]):
                assert reveal["round"] == 1
                assert reveal["answered"] is False
                assert reveal["answer_feedback"]["selected"] is None
                assert all(player["score"] == 0 for player in reveal["players"])


def test_elimination_websocket_synchronizes_mode_and_broadcasts_elimination_reveal() -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 100.0
            self.wall = 1_800_000_000.0

        def monotonic(self) -> float:
            return self.now

        def wall_time(self) -> float:
            return self.wall

        def advance(self, seconds: float) -> None:
            self.now += seconds
            self.wall += seconds

    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall_time)
    host = asyncio.run(engine.create_room("Nova", mode="elimination"))
    guest = asyncio.run(engine.join_room(host["state"]["code"], "Orbit"))
    code = host["state"]["code"]
    asyncio.run(engine.start_game(code, host["player_id"]))
    clock.advance(COUNTDOWN_SECONDS)
    asyncio.run(engine.advance_due())
    correct = engine._rooms[code].questions[0].answer
    app = create_app(engine=engine)

    async def pause_scheduler(stop: asyncio.Event) -> None:
        await stop.wait()

    app.state.hub.scheduler = pause_scheduler

    def receive_matching(socket, predicate):
        for _ in range(12):
            message = socket.receive_json()
            if predicate(message):
                return message
        raise AssertionError("Expected WebSocket event was not received")

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as host_socket, client.websocket_connect("/ws") as guest_socket:
            host_socket.send_json({"event": "reconnect", "token": host["token"]})
            host_joined = receive_matching(host_socket, lambda message: message.get("event") == "joined")
            guest_socket.send_json({"event": "reconnect", "token": guest["token"]})
            guest_joined = receive_matching(guest_socket, lambda message: message.get("event") == "joined")
            assert host_joined["state"]["mode"] == guest_joined["state"]["mode"] == "elimination"
            assert host_joined["state"]["deadline"] == guest_joined["state"]["deadline"]
            assert host_joined["state"]["question_seconds"] == 15.0
            assert host_joined["state"]["momentum"]["enabled"] is False

            host_socket.send_json({"event": "answer", "choice": (correct + 1) % 4})
            receive_matching(host_socket, lambda message: message.get("event") == "answer-update")
            guest_socket.send_json({"event": "answer", "choice": correct})
            host_reveal = receive_matching(
                host_socket,
                lambda message: message.get("event") == "answer-update" and message.get("state", {}).get("phase") == "reveal",
            )
            guest_reveal = receive_matching(
                guest_socket,
                lambda message: message.get("event") == "answer-update" and message.get("state", {}).get("phase") == "reveal",
            )
            for reveal in (host_reveal["state"], guest_reveal["state"]):
                assert reveal["remaining_players"] == 1
                eliminated = next(player for player in reveal["players"] if player["id"] == host["player_id"])
                assert eliminated["eliminated"] is True
                assert eliminated["elimination_reason"] == "incorrect"
                assert "answer" not in reveal["question"]


def test_bluff_master_websocket_actions_keep_voting_anonymous_and_reveal_scores() -> None:
    app = create_app()

    def receive_matching(websocket, predicate):
        for _ in range(20):
            message = websocket.receive_json()
            if predicate(message):
                return message
        raise AssertionError("Expected WebSocket event was not received")

    with TestClient(app) as client:
        sockets = [client.websocket_connect("/ws") for _ in range(3)]
        with sockets[0] as host, sockets[1] as guest, sockets[2] as third:
            host.send_json({"event": "create", "name": "Bluff Host", "mode": "bluff_master"})
            joined = receive_matching(host, lambda message: message.get("event") == "joined")
            code = joined["state"]["code"]
            for websocket, name in ((guest, "Bluff Guest"), (third, "Bluff Third")):
                websocket.send_json({"event": "join", "name": name, "code": code})
                receive_matching(websocket, lambda message: message.get("event") == "joined")
            host.send_json({"event": "start"})
            receive_matching(host, lambda message: message.get("state", {}).get("phase") == "submission")
            for websocket, answer, action_id in (
                (host, "a violet lantern", "ws-bluff-submit-0001"),
                (guest, "a quiet waterfall", "ws-bluff-submit-0002"),
                (third, "a silver compass", "ws-bluff-submit-0003"),
            ):
                websocket.send_json({"event": "bluff_submit", "answer": answer, "request_id": action_id})
            voting = [
                receive_matching(websocket, lambda message: message.get("event") == "bluff-submit" and message.get("state", {}).get("phase") == "voting")["state"]
                for websocket in (host, guest, third)
            ]
            for state in voting:
                assert "correct_answer" not in state["bluff"]
                assert all("author_id" not in item and "is_correct" not in item for item in state["bluff"]["choices"])
                assert "answer" not in state["question"]
            for index, (websocket, state) in enumerate(zip((host, guest, third), voting)):
                choice = next(item for item in state["bluff"]["choices"] if not item["is_yours"])
                websocket.send_json({"event": "bluff_vote", "choice_id": choice["id"], "request_id": f"ws-bluff-vote-{index:04d}-01"})
            revealed = [
                receive_matching(websocket, lambda message: message.get("event") == "bluff-vote" and message.get("state", {}).get("phase") == "reveal")["state"]
                for websocket in (host, guest, third)
            ]
            assert all(isinstance(state["bluff"]["correct_answer"], str) for state in revealed)
            assert all(any(item["author"] == "Bluff Host" for item in state["bluff"]["results"]) for state in revealed)


def test_escape_room_websocket_hint_and_shared_progress() -> None:
    app = create_app()

    def receive_matching(websocket, predicate):
        for _ in range(20):
            message = websocket.receive_json()
            if predicate(message):
                return message
        raise AssertionError("Expected WebSocket event was not received")

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as host, client.websocket_connect("/ws") as guest:
            host.send_json({"event": "create", "name": "Escape Host", "mode": "escape_room"})
            joined = receive_matching(host, lambda message: message.get("event") == "joined")
            code = joined["state"]["code"]
            guest.send_json({"event": "join", "name": "Escape Guest", "code": code})
            receive_matching(guest, lambda message: message.get("event") == "joined")
            host.send_json({"event": "start"})
            start = receive_matching(host, lambda message: message.get("state", {}).get("phase") == "escape_puzzle")
            assert start["state"]["question_seconds"] == 900
            host.send_json({"event": "escape_hint", "request_id": "ws-escape-hint-request-01"})
            hint_host = receive_matching(host, lambda message: message.get("event") == "escape-hint")["state"]
            hint_guest = receive_matching(guest, lambda message: message.get("event") == "escape-hint")["state"]
            assert hint_host["escape"]["hints_remaining"] == hint_guest["escape"]["hints_remaining"] == 2
            assert hint_host["escape"]["current_puzzle"]["hint"] == hint_guest["escape"]["current_puzzle"]["hint"]
            host.send_json({"event": "escape_answer", "answer": "wrong", "puzzle_index": 0, "request_id": "ws-escape-wrong-answer-01"})
            wrong = receive_matching(host, lambda message: message.get("event") == "escape-answer")["state"]
            assert wrong["escape"]["solved_puzzles"] == 0 and wrong["escape"]["hints_remaining"] == 2
            receive_matching(guest, lambda message: message.get("event") == "escape-answer")
            correct = ESCAPE_QUESTIONS[0].format_payload["solution"]
            guest.send_json({"event": "escape_answer", "answer": correct, "puzzle_index": 0, "request_id": "ws-escape-correct-answer-01"})
            progressed = receive_matching(guest, lambda message: message.get("event") == "escape-answer")["state"]
            assert progressed["escape"]["solved_puzzles"] == 1
            assert progressed["escape"]["current_puzzle"]["index"] == 1


def test_websocket_rejects_bad_origin_unjoined_actions_and_oversized_payload(server_url: str) -> None:
    async def exercise() -> None:
        with pytest.raises(InvalidStatus):
            await websockets.connect(server_url.replace("http://", "ws://") + "/ws", origin="https://attacker.example")

        client = await open_client(server_url)
        try:
            await client.send("{bad json")
            malformed = await receive_until(client, lambda item: item.get("event") == "error")
            assert malformed["code"] == "invalid_json"
            await client.send('{"event":"start"}')
            unauthorized = await receive_until(client, lambda item: item.get("event") == "error")
            assert unauthorized["code"] == "join_first"
            await client.send("x" * 5000)
            try:
                response = await receive_until(client, lambda item: item.get("event") == "error", timeout=2)
            except ConnectionClosedError as closed:
                assert closed.rcvd.code == 1009
            else:
                assert response["code"] == "message_too_large"
                try:
                    await asyncio.wait_for(client.recv(), timeout=2)
                except ConnectionClosedError as closed:
                    assert closed.rcvd.code == 1009
                else:
                    raise AssertionError("Oversized message did not close the WebSocket")
        finally:
            await client.close()

    asyncio.run(exercise())


def test_websocket_event_rate_limit(server_url: str) -> None:
    async def exercise() -> None:
        client = await open_client(server_url)
        try:
            for _ in range(30):
                await client.send('{"event":"unrecognized"}')
                await receive_until(client, lambda item: item.get("event") == "error")
            await client.send('{"event":"start"}')
            limited = await receive_until(client, lambda item: item.get("event") == "error")
            assert limited["code"] == "rate_limited"
            try:
                await asyncio.wait_for(client.recv(), timeout=2)
            except ConnectionClosedError as closed:
                assert closed.rcvd.code == 1008
            else:
                raise AssertionError("Rate-limited WebSocket remained open")
        finally:
            await client.close()

    asyncio.run(exercise())

from __future__ import annotations

import asyncio

import pytest

from quizverse.engine import GameEngine, GameError, Phase
from quizverse.modes import resolve_mode


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_now = 20.0
        self.wall_now = 1_800_000_000.0

    def monotonic(self) -> float:
        return self.monotonic_now

    def wall(self) -> float:
        return self.wall_now

    def advance(self, seconds: float) -> None:
        self.monotonic_now += seconds
        self.wall_now += seconds


ROSTERS = {
    "classic": 2,
    "speed_challenge": 2,
    "team_battle": 4,
    "elimination": 2,
    "solo_practice": 1,
    "quiz_royale": 4,
    "power_play": 2,
    "bluff_master": 3,
    "escape_room": 2,
}


def run_empty_match(mode: str):
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    roster = ROSTERS[mode]
    first = asyncio.run(engine.create_room("Host", mode, profile_id=f"{mode[:20]:<20}"[:20] + "a" * 12))
    code = first["state"]["code"]
    players = [first]
    for index in range(1, roster):
        players.append(engine_join(engine, code, f"Guest {index}", f"{mode[:20]:<20}"[:20] + f"{index:012d}"))
    asyncio.run(engine.start_game(code, first["player_id"]))
    for _ in range(100):
        state = asyncio.run(engine.snapshot(code, first["player_id"]))
        if state["phase"] == Phase.RESULTS:
            break
        room = engine._rooms[code]
        assert room.deadline is not None
        clock.advance(max(0.001, room.deadline - clock.monotonic()))
        asyncio.run(engine.advance_due())
    state = asyncio.run(engine.snapshot(code, first["player_id"]))
    assert state["phase"] == Phase.RESULTS
    assert "profile_id" not in state and all("profile_id" not in item for item in state["players"])
    return engine, clock, first, state


def engine_join(engine: GameEngine, code: str, name: str, profile_id: str) -> dict:
    return asyncio.run(engine.join_room(code, name, profile_id=profile_id))


@pytest.mark.parametrize("mode", list(ROSTERS))
def test_terminal_result_payload_preserves_all_nine_mode_semantics(mode: str):
    engine, _, host, state = run_empty_match(mode)
    payloads = asyncio.run(engine.claim_completed_match_results())
    assert len(payloads) == 1
    result = payloads[0]
    assert result["mode"] == mode and result["status"] == "completed"
    assert result["question_set_hash"] and len(result["question_set_hash"]) == 64
    assert len(result["participants"]) == ROSTERS[mode]
    if mode == "team_battle":
        assert result["result_kind"] == "team" and len(result["teams"]) == 2
        assert all(row["placement"] is None for row in result["participants"])
    elif mode == "escape_room":
        assert result["result_kind"] == "cooperative"
        assert all(row["placement"] is None and row["final_score"] is None for row in result["participants"])
        assert result["result_context"]["outcome"] == "failure"
    elif mode == "solo_practice":
        assert result["result_kind"] == "practice"
        assert all(row["outcome"] == "practice" and row["placement"] is None for row in result["participants"])
    else:
        assert result["result_kind"] == "individual"
        assert all(row["placement"] is not None for row in result["participants"])
    assert state["match_history"]["status"] == "pending"
    assert "credential" not in repr(result).casefold()
    assert asyncio.run(engine.mark_match_result_saved(result["engine_match_id"])) == host["state"]["code"]


def test_profile_binding_is_optional_immutable_unique_and_reconnect_survives():
    engine = GameEngine()
    host = asyncio.run(engine.create_room("Host", profile_id="a" * 32))
    code = host["state"]["code"]
    with pytest.raises(GameError, match="already has a seat"):
        asyncio.run(engine.join_room(code, "Second", profile_id="a" * 32))
    guest = asyncio.run(engine.join_room(code, "Guest"))
    assert engine._rooms[code].players[host["player_id"]].profile_id == "a" * 32
    assert engine._rooms[code].players[guest["player_id"]].profile_id is None
    asyncio.run(engine.disconnect(code, host["player_id"]))
    restored = asyncio.run(engine.reconnect(host["token"]))
    assert restored["player_id"] == host["player_id"]
    assert engine._rooms[code].players[host["player_id"]].profile_id == "a" * 32


def test_profile_linked_result_is_not_cleared_before_persistence_and_rematch_gets_new_id():
    engine, clock, host, state = run_empty_match("classic")
    code = host["state"]["code"]
    result = asyncio.run(engine.claim_completed_match_results())[0]
    with pytest.raises(GameError, match="still being saved"):
        asyncio.run(engine.set_lobby(code, host["player_id"], rematch=True))
    asyncio.run(engine.mark_match_result_failed(result["engine_match_id"], "persistence_unavailable"))
    pending = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert pending["match_history"]["status"] == "retrying"
    clock.advance(30)
    retry = asyncio.run(engine.claim_completed_match_results())[0]
    assert retry["engine_match_id"] == result["engine_match_id"]
    asyncio.run(engine.mark_match_result_saved(result["engine_match_id"]))
    asyncio.run(engine.set_lobby(code, host["player_id"], rematch=True))
    asyncio.run(engine.start_game(code, host["player_id"]))
    next_match = engine._rooms[code].engine_match_id
    assert next_match and next_match != result["engine_match_id"]

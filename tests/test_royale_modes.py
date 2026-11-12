import asyncio

import pytest

from quizverse.engine import GameEngine, GameError, Phase
from quizverse.modes import GameMode, resolve_mode


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def wall(self):
        return 1_800_000_000 + self.now - 100

    def advance(self, amount):
        self.now += amount


def room(mode, count):
    clock = Clock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    players = [asyncio.run(engine.create_room("Player 0", mode=mode))]
    for index in range(1, count):
        players.append(asyncio.run(engine.join_room(players[0]["state"]["code"], f"Player {index}")))
    return engine, clock, players


def question(engine, clock, players):
    code = players[0]["state"]["code"]
    asyncio.run(engine.start_game(code, players[0]["player_id"]))
    clock.advance(3)
    asyncio.run(engine.advance_due())
    current = engine._rooms[code].questions[0]
    return code, current


def test_royale_config_capacity_and_three_lives_to_last_player():
    assert resolve_mode("quiz_royale").max_players == 20
    assert resolve_mode("quiz_royale").question_count == 15
    engine, clock, players = room("quiz_royale", 4)
    code, _ = question(engine, clock, players)
    state = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert state["max_players"] == 20
    assert {player["lives"] for player in state["players"]} == {3}
    for round_index in range(3):
        current = engine._rooms[code].questions[round_index]
        for index, player in enumerate(players):
            choice = current.answer if index == 0 else (current.answer + 1) % 4
            state = asyncio.run(engine.submit_answer(code, player["player_id"], choice))
        assert state["phase"] == Phase.REVEAL
        if round_index < 2:
            clock.advance(3)
            asyncio.run(engine.advance_due())
    assert state["remaining_players"] == 1
    survivors = [player for player in state["players"] if not player["eliminated"]]
    assert [player["id"] for player in survivors] == [players[0]["player_id"]]
    clock.advance(3)
    results = asyncio.run(engine.advance_due())
    assert all(item["phase"] == Phase.RESULTS for item in results)
    assert engine._rooms[code].elimination_end_reason == "one_survivor"


@pytest.mark.parametrize("count", [8, 20])
def test_royale_allows_eight_and_twenty_but_rejects_over_capacity(count):
    engine, _, players = room("quiz_royale", count)
    assert len(players) == count
    if count == 20:
        with pytest.raises(GameError, match="full"):
            asyncio.run(engine.join_room(players[0]["state"]["code"], "Overflow"))
    classic_engine = GameEngine()
    classic = asyncio.run(classic_engine.create_room("Classic"))
    for index in range(7):
        asyncio.run(classic_engine.join_room(classic["state"]["code"], f"C{index}"))
    with pytest.raises(GameError, match="full"):
        asyncio.run(classic_engine.join_room(classic["state"]["code"], "Ninth"))


def test_royale_requires_four_connected_players():
    engine, _, players = room("quiz_royale", 3)
    with pytest.raises(GameError, match="four connected"):
        asyncio.run(engine.start_game(players[0]["state"]["code"], players[0]["player_id"]))


def test_royale_simultaneous_all_out_is_a_shared_terminal_placement():
    engine, clock, players = room("quiz_royale", 4)
    code, _ = question(engine, clock, players)
    for round_index in range(3):
        current = engine._rooms[code].questions[round_index]
        for player in players:
            state = asyncio.run(engine.submit_answer(code, player["player_id"], (current.answer + 1) % 4))
        if round_index < 2:
            clock.advance(3)
            asyncio.run(engine.advance_due())
    assert state["remaining_players"] == 0
    clock.advance(3)
    final = asyncio.run(engine.advance_due())
    result = next(item for item in final if item["phase"] == Phase.RESULTS)
    assert result["results"]["end_reason"] == "all_eliminated"
    assert result["results"]["is_draw"] is True
    assert {player["placement"] for player in result["results"]["leaderboard"]} == {1}

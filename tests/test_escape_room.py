import asyncio
from dataclasses import replace

import pytest

from quizverse.engine import GameEngine, GameError, Phase
from quizverse.modes import resolve_mode
from quizverse.special_questions import ESCAPE_QUESTIONS


class Clock:
    def __init__(self): self.now = 50.0
    def monotonic(self): return self.now
    def wall(self): return 1_800_000_000 + self.now - 50
    def advance(self, amount): self.now += amount


def room(count=2, *, selection=None):
    clock = Clock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Player 0", mode="escape_room", selection=selection))
    players = [host]
    for index in range(1, count): players.append(asyncio.run(engine.join_room(host["state"]["code"], f"Player {index}")))
    return engine, clock, players


def start(engine, clock, players):
    code = players[0]["state"]["code"]
    asyncio.run(engine.start_game(code, players[0]["player_id"]))
    clock.advance(3)
    state = asyncio.run(engine.advance_due())[0]
    assert state["phase"] == Phase.ESCAPE_PUZZLE
    return code


def test_escape_config_roster_limit_and_exact_validated_set():
    config = resolve_mode("escape_room")
    assert (config.min_players, config.max_players, config.question_count, config.question_seconds) == (2, 8, 10, 900)
    engine, clock, players = room(8)
    with pytest.raises(GameError, match="full"):
        asyncio.run(engine.join_room(players[0]["state"]["code"], "Overflow"))
    with pytest.raises(GameError, match="exactly ten"):
        asyncio.run(GameEngine().create_room("Host", "escape_room", {"question_count": 9}))
    code = start(engine, clock, players)
    questions = engine._rooms[code].questions
    assert len(questions) == 10
    assert [question.format_payload["puzzle_index"] for question in questions] == list(range(10))
    assert {question.format_payload["room_index"] for question in questions} == {1, 2, 3, 4, 5}
    short_engine, _, short_players = room(1)
    with pytest.raises(GameError, match="two connected players"):
        asyncio.run(short_engine.start_game(short_players[0]["state"]["code"], short_players[0]["player_id"]))


def test_escape_set_rejects_incomplete_missing_dependency_and_cycles():
    from quizverse.question_intelligence import SelectionFilters

    filters = SelectionFilters(count=10, question_format="puzzle", enforce_reviewed=True)
    assert not GameEngine._valid_escape_set(list(ESCAPE_QUESTIONS[:-1]))
    missing = list(ESCAPE_QUESTIONS)
    missing[1] = replace(missing[1], format_payload={**missing[1].format_payload, "dependencies": ["missing-puzzle"]})
    assert not GameEngine._valid_escape_set(missing)
    cyclic = list(ESCAPE_QUESTIONS)
    cyclic[0] = replace(cyclic[0], format_payload={**cyclic[0].format_payload, "dependencies": [cyclic[-1].id]})
    assert not GameEngine._valid_escape_set(cyclic)
    assert GameEngine._select_escape_puzzle_set(ESCAPE_QUESTIONS, filters) == ESCAPE_QUESTIONS


def test_escape_answer_progression_hints_rate_limit_concurrency_and_victory():
    engine, clock, players = room()
    code = start(engine, clock, players)
    host, guest = (player["player_id"] for player in players)
    first = asyncio.run(engine.snapshot(code, host))
    assert first["escape"]["current_puzzle"]["index"] == 0
    assert len(first["escape"]["rooms"]) == 5
    assert "solution" not in str(first)
    assert "canonical_answer" not in str(first)
    hint_state = asyncio.run(engine.request_escape_hint(code, guest, "escape-hint-request-0001"))
    assert hint_state["escape"]["hints_remaining"] == 2
    assert hint_state["escape"]["current_puzzle"]["hint"]
    with pytest.raises(GameError, match="already used"):
        asyncio.run(engine.request_escape_hint(code, guest, "escape-hint-request-0001"))
    with pytest.raises(GameError, match="already used a hint"):
        asyncio.run(engine.request_escape_hint(code, host, "escape-hint-request-0002"))
    wrong = asyncio.run(engine.submit_escape_answer(code, host, "definitely wrong", 0, "escape-wrong-answer-0001"))
    assert wrong["escape"]["solved_puzzles"] == 0 and wrong["escape"]["hints_remaining"] == 2
    with pytest.raises(GameError, match="Wait a moment"):
        asyncio.run(engine.submit_escape_answer(code, host, "still wrong", 0, "escape-wrong-answer-0002"))
    clock.advance(0.8)
    with pytest.raises(GameError, match="between 1 and 200"):
        asyncio.run(engine.submit_escape_answer(code, host, "\x00", 0, "escape-invalid-answer-01"))
    clock.advance(0.8)
    first_solution = engine._rooms[code].questions[0].format_payload["solution"]
    async def submit_concurrently():
        return await asyncio.gather(
            engine.submit_escape_answer(code, host, first_solution, 0, "escape-concurrent-answer-01"),
            engine.submit_escape_answer(code, guest, first_solution, 0, "escape-concurrent-answer-02"),
            return_exceptions=True,
        )
    outcomes = asyncio.run(submit_concurrently())
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    assert sum(isinstance(item, GameError) and item.code == "stale_puzzle" for item in outcomes) == 1
    state = asyncio.run(engine.snapshot(code, host))
    assert state["escape"]["solved_puzzles"] == 1
    assert state["escape"]["current_puzzle"]["index"] == 1
    assert state["escape"]["current_puzzle"]["hint"] is None
    with pytest.raises(GameError, match="already changed"):
        asyncio.run(engine.submit_escape_answer(code, guest, "10", 0, "escape-stale-answer-0001"))
    for index in range(1, 10):
        clock.advance(0.8)
        solution = engine._rooms[code].questions[index].format_payload["solution"]
        state = asyncio.run(engine.submit_escape_answer(code, guest, solution, index, f"escape-solve-{index:02d}-request"))
    assert state["phase"] == Phase.RESULTS
    assert state["results"] == {
        "outcome": "victory", "completed_rooms": 5, "solved_puzzles": 10,
        "remaining_hints": 2, "elapsed_seconds": 8, "team_outcome": "victory",
    }
    assert "solution" not in str(state)


def test_escape_reconnect_failure_timeout_and_rematch():
    engine, clock, players = room()
    code = start(engine, clock, players)
    token = players[1]["token"]
    asyncio.run(engine.request_escape_hint(code, players[0]["player_id"], "escape-reconnect-hint-001"))
    asyncio.run(engine.disconnect(code, players[1]["player_id"]))
    restored = asyncio.run(engine.reconnect(token))["state"]
    assert restored["phase"] == Phase.ESCAPE_PUZZLE
    assert restored["escape"]["current_puzzle"]["index"] == 0
    assert restored["escape"]["hints_remaining"] == 2
    clock.advance(900)
    states = asyncio.run(engine.advance_due())
    assert all(item["phase"] == Phase.RESULTS for item in states)
    assert states[0]["results"]["outcome"] == "failure"
    assert states[0]["results"]["solved_puzzles"] == 0
    asyncio.run(engine.set_lobby(code, players[0]["player_id"], rematch=True))
    lobby = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert lobby["phase"] == Phase.WAITING
    assert lobby["escape"]["hints_remaining"] == 3


def test_escape_shared_hint_budget_is_atomic_and_never_negative():
    engine, clock, players = room()
    code = start(engine, clock, players)
    for index in range(3):
        state = asyncio.run(engine.request_escape_hint(code, players[index % 2]["player_id"], f"shared-hint-request-{index:04d}"))
        assert state["escape"]["hints_remaining"] == 2-index
        solution = engine._rooms[code].questions[index].format_payload["solution"]
        clock.advance(0.8)
        state = asyncio.run(engine.submit_escape_answer(code, players[1]["player_id"], solution, index, f"shared-hint-solve-{index:04d}"))
    with pytest.raises(GameError, match="used all three"):
        asyncio.run(engine.request_escape_hint(code, players[0]["player_id"], "shared-hint-request-0004"))
    assert state["escape"]["hints_remaining"] == 0


def test_escape_rejects_unreviewed_or_filter_mismatched_content_before_countdown():
    unreviewed = tuple(replace(question, review_status="unreviewed") for question in ESCAPE_QUESTIONS)
    clock = Clock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall, question_provider=lambda _: unreviewed)
    host = asyncio.run(engine.create_room("Host", "escape_room"))
    guest = asyncio.run(engine.join_room(host["state"]["code"], "Guest"))
    with pytest.raises(GameError, match="eligible questions"):
        asyncio.run(engine.start_game(host["state"]["code"], host["player_id"]))
    assert asyncio.run(engine.snapshot(host["state"]["code"], guest["player_id"]))["phase"] == Phase.WAITING

    partial_engine = GameEngine(question_provider=lambda _: ESCAPE_QUESTIONS[:9])
    partial_host = asyncio.run(partial_engine.create_room("Host", "escape_room"))
    asyncio.run(partial_engine.join_room(partial_host["state"]["code"], "Guest"))
    with pytest.raises(GameError, match="complete reviewed Escape Room puzzle set"):
        asyncio.run(partial_engine.start_game(partial_host["state"]["code"], partial_host["player_id"]))
    assert asyncio.run(partial_engine.snapshot(partial_host["state"]["code"], partial_host["player_id"]))["phase"] == Phase.WAITING

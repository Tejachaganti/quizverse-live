import asyncio
from dataclasses import replace

import pytest

from quizverse.engine import GameEngine, GameError, Phase
from quizverse.modes import resolve_mode
from quizverse.special_questions import BLUFF_QUESTIONS


class Clock:
    def __init__(self): self.now = 10.0
    def monotonic(self): return self.now
    def wall(self): return 1_800_000_000 + self.now - 10
    def advance(self, amount): self.now += amount


def room(count=3, *, question_count=10, selection_language="en"):
    clock = Clock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Player 0", mode="bluff_master", selection={"question_count": question_count, "language": selection_language}))
    players = [host]
    for index in range(1, count):
        players.append(asyncio.run(engine.join_room(host["state"]["code"], f"Player {index}")))
    return engine, clock, players


def start(engine, clock, players):
    code = players[0]["state"]["code"]
    asyncio.run(engine.start_game(code, players[0]["player_id"]))
    clock.advance(3)
    state = asyncio.run(engine.advance_due())[0]
    assert state["phase"] == Phase.SUBMISSION
    return code


def submit_all(engine, players, answers=("a silver comet", "a hidden garden", "a quiet river")):
    code = players[0]["state"]["code"]
    for index, player in enumerate(players):
        asyncio.run(engine.submit_bluff(code, player["player_id"], answers[index], f"submission-request-{index:04d}"))


def test_bluff_master_limits_and_fixed_round_count():
    config = resolve_mode("bluff_master")
    assert (config.min_players, config.max_players, config.question_count) == (3, 8, 10)
    with pytest.raises(GameError, match="exactly ten"):
        asyncio.run(GameEngine().create_room("Host", "bluff_master", {"question_count": 9}))
    short_engine, _, short_players = room(2)
    with pytest.raises(GameError, match="three connected players"):
        asyncio.run(short_engine.start_game(short_players[0]["state"]["code"], short_players[0]["player_id"]))
    engine, _, players = room(8)
    with pytest.raises(GameError, match="full"):
        asyncio.run(engine.join_room(players[0]["state"]["code"], "Overflow"))


def test_bluff_submission_is_validated_private_and_voting_is_anonymous():
    engine, clock, players = room()
    code = start(engine, clock, players)
    question = engine._rooms[code].questions[0]
    canonical = question.format_payload["canonical_answer"]
    with pytest.raises(GameError, match="real answer"):
        asyncio.run(engine.submit_bluff(code, players[0]["player_id"], canonical, "rejected-real-0001"))
    with pytest.raises(GameError, match="between 2 and 120"):
        asyncio.run(engine.submit_bluff(code, players[0]["player_id"], "", "rejected-empty-01"))
    asyncio.run(engine.submit_bluff(code, players[0]["player_id"], "a silver comet", "accepted-fake-0001"))
    with pytest.raises(GameError, match="already submitted"):
        asyncio.run(engine.submit_bluff(code, players[0]["player_id"], "another answer", "accepted-fake-0002"))
    with pytest.raises(GameError, match="already submitted"):
        asyncio.run(engine.submit_bluff(code, players[1]["player_id"], "A SILVER   COMET", "duplicate-fake-001"))
    asyncio.run(engine.submit_bluff(code, players[1]["player_id"], "a hidden garden", "accepted-fake-0003"))
    state = asyncio.run(engine.submit_bluff(code, players[2]["player_id"], "a quiet river", "accepted-fake-0004"))
    assert state["phase"] == Phase.VOTING
    assert all("author_id" not in option and "is_correct" not in option for option in state["bluff"]["choices"])
    assert "correct_answer" not in state["bluff"]
    assert "answer" not in state["question"]
    assert len(state["bluff"]["choices"]) == 4
    internal = engine._rooms[code].bluff_options
    assert sum(bool(item["is_correct"]) for item in internal) == 1
    real_option = next(item for item in internal if item["is_correct"])
    fake_by_host = next(item for item in internal if item["author_id"] == players[0]["player_id"])
    with pytest.raises(GameError, match="own fake"):
        asyncio.run(engine.cast_bluff_vote(code, players[0]["player_id"], fake_by_host["id"], "self-vote-request-01"))
    asyncio.run(engine.cast_bluff_vote(code, players[0]["player_id"], real_option["id"], "correct-vote-0001"))
    with pytest.raises(GameError, match="already locked"):
        asyncio.run(engine.cast_bluff_vote(code, players[0]["player_id"], real_option["id"], "duplicate-vote-001"))
    asyncio.run(engine.cast_bluff_vote(code, players[1]["player_id"], fake_by_host["id"], "fake-vote-000001"))
    state = asyncio.run(engine.cast_bluff_vote(code, players[2]["player_id"], fake_by_host["id"], "fake-vote-000002"))
    assert state["phase"] == Phase.REVEAL
    assert state["bluff"]["correct_answer"] == canonical
    assert next(player for player in state["players"] if player["id"] == players[0]["player_id"])["score"] == 2000
    fake_result = next(item for item in state["bluff"]["results"] if item["author"] == "Player 0")
    assert fake_result["fooled_players"] == ["Player 1", "Player 2"]


def test_bluff_missing_submissions_votes_deadlines_and_reconnect():
    engine, clock, players = room()
    code = start(engine, clock, players)
    asyncio.run(engine.submit_bluff(code, players[0]["player_id"], "a striped moon", "deadline-fake-0001"))
    token = players[1]["token"]
    asyncio.run(engine.disconnect(code, players[1]["player_id"]))
    rejoined = asyncio.run(engine.reconnect(token))
    assert rejoined["state"]["phase"] == Phase.SUBMISSION
    clock.advance(30)
    snapshots = asyncio.run(engine.advance_due())
    assert all(item["phase"] == Phase.VOTING for item in snapshots)
    assert len(snapshots[0]["bluff"]["choices"]) == 2
    assert all("answer" not in item["question"] for item in snapshots)
    clock.advance(15)
    snapshots = asyncio.run(engine.advance_due())
    assert all(item["phase"] == Phase.REVEAL for item in snapshots)
    assert all(item["players"][0]["score"] == 0 for item in snapshots)
    clock.advance(5)
    final = asyncio.run(engine.advance_due())
    assert all(item["phase"] == Phase.SUBMISSION and item["round"] == 2 for item in final)


def test_bluff_requires_reviewed_inventory_and_host_filters_before_countdown():
    unreviewed = tuple(replace(question, review_status="unreviewed") for question in BLUFF_QUESTIONS)
    clock = Clock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall, question_provider=lambda _: unreviewed)
    host = asyncio.run(engine.create_room("Host", "bluff_master"))
    asyncio.run(engine.join_room(host["state"]["code"], "Guest 1"))
    asyncio.run(engine.join_room(host["state"]["code"], "Guest 2"))
    with pytest.raises(GameError, match="eligible questions"):
        asyncio.run(engine.start_game(host["state"]["code"], host["player_id"]))
    assert asyncio.run(engine.snapshot(host["state"]["code"], host["player_id"]))["phase"] == Phase.WAITING

    language_engine, _, players = room(selection_language="fr")
    with pytest.raises(GameError, match="eligible questions"):
        asyncio.run(language_engine.start_game(players[0]["state"]["code"], players[0]["player_id"]))


def test_bluff_replayed_action_rejected_and_rematch_resets():
    engine, clock, players = room()
    code = start(engine, clock, players)
    request = "same-request-id-0001"
    asyncio.run(engine.submit_bluff(code, players[0]["player_id"], "a paper crown", request))
    with pytest.raises(GameError, match="already used"):
        asyncio.run(engine.submit_bluff(code, players[1]["player_id"], "a tiny sailboat", request))
    for round_index in range(10):
        room_state = engine._rooms[code]
        for index, player in enumerate(players):
            if player["player_id"] not in room_state.bluff_submissions:
                asyncio.run(engine.submit_bluff(code, player["player_id"], f"idea {round_index} by player {index}", f"round-{round_index:02d}-fake-{index:02d}"))
        room_state = engine._rooms[code]
        for index, player in enumerate(players):
            option = next(item for item in room_state.bluff_options if item["author_id"] != player["player_id"])
            asyncio.run(engine.cast_bluff_vote(code, player["player_id"], option["id"], f"round-{round_index:02d}-vote-{index:02d}"))
        clock.advance(5)
        asyncio.run(engine.advance_due())
    assert asyncio.run(engine.snapshot(code, players[0]["player_id"]))["phase"] == Phase.RESULTS
    asyncio.run(engine.set_lobby(code, players[0]["player_id"], rematch=True))
    state = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert state["phase"] == Phase.WAITING
    assert all(player["score"] == 0 for player in state["players"])


def test_bluff_retains_disconnected_authors_until_results_are_safe():
    engine, clock, players = room()
    code = start(engine, clock, players)
    submit_all(engine, players)
    room_state = engine._rooms[code]
    author_id = players[1]["player_id"]
    own_fake = next(item for item in room_state.bluff_options if item["author_id"] == author_id)
    for index, player in enumerate(players):
        choice = next(item for item in room_state.bluff_options if item["id"] != own_fake["id"] and item["author_id"] != player["player_id"])
        asyncio.run(engine.cast_bluff_vote(code, player["player_id"], choice["id"], f"disconnect-result-vote-{index:02d}"))
    assert asyncio.run(engine.snapshot(code, players[0]["player_id"]))["phase"] == Phase.REVEAL
    asyncio.run(engine.disconnect(code, author_id))
    clock.advance(61)
    asyncio.run(engine.cleanup())
    assert author_id in engine._rooms[code].players
    state = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert any(item["author"] == "Player 1" for item in state["bluff"]["results"])

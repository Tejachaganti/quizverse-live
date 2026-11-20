import asyncio
import pytest

from quizverse.engine import GameEngine, Phase
from quizverse.modes import (
    CLASSIC_CONFIG,
    SPEED_CHALLENGE_CONFIG,
    TEAM_BATTLE_CONFIG,
    ELIMINATION_CONFIG,
    SOLO_PRACTICE_CONFIG,
    QUIZ_ROYALE_CONFIG,
    POWER_PLAY_CONFIG,
    BLUFF_MASTER_CONFIG,
    ESCAPE_ROOM_CONFIG,
    GameMode,
    resolve_mode,
)
from quizverse.questions import Question


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_now = 100.0
        self.wall_now = 1_800_000_000.0

    def monotonic(self) -> float:
        return self.monotonic_now

    def wall(self) -> float:
        return self.wall_now

    def advance(self, seconds: float) -> None:
        self.monotonic_now += seconds
        self.wall_now += seconds


def test_review_access_control():
    """Verify that review data and answer keys are not leaked before Phase.RESULTS."""
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Alice"))
    code = host["state"]["code"]
    asyncio.run(engine.join_room(code, "Bob"))

    # Phase: WAITING
    snap = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert snap.get("review") is None
    assert "review" not in (snap.get("results") or {})

    # Start game -> COUNTDOWN
    asyncio.run(engine.start_game(code, host["player_id"]))
    snap = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert snap.get("review") is None

    # Countdown -> QUESTION
    clock.advance(3.1)
    asyncio.run(engine.advance_due())
    snap = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert snap["phase"] == Phase.QUESTION
    assert snap.get("review") is None


def test_review_correct_and_incorrect_answers():
    """Verify review captures correct vs incorrect answers and accuracy metrics."""
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Alice"))
    code = host["state"]["code"]
    guest = asyncio.run(engine.join_room(code, "Bob"))
    h_id = host["player_id"]
    g_id = guest["player_id"]

    asyncio.run(engine.start_game(code, h_id))
    clock.advance(3.1)
    asyncio.run(engine.advance_due())

    # Play 10 rounds
    for _ in range(10):
        room = engine._rooms[code]
        assert room.phase == Phase.QUESTION
        q = room.questions[room.round_index]
        correct_choice = q.answer
        incorrect_choice = (correct_choice + 1) % len(q.options)

        # Alice answers correctly
        asyncio.run(engine.submit_answer(code, h_id, correct_choice))
        # Bob answers incorrectly
        asyncio.run(engine.submit_answer(code, g_id, incorrect_choice))

        # Advance Reveal
        clock.advance(room.match_config.reveal_seconds)
        asyncio.run(engine.advance_due())

    room = engine._rooms[code]
    assert room.phase == Phase.RESULTS

    # Alice snapshot review
    alice_snap = asyncio.run(engine.snapshot(code, h_id))
    assert alice_snap.get("review") is not None
    alice_review = alice_snap["review"]
    assert alice_review["summary"]["total_questions"] == 10
    assert alice_review["summary"]["correct_answers"] == 10
    assert alice_review["summary"]["incorrect_answers"] == 0
    assert alice_review["summary"]["unanswered_questions"] == 0
    assert alice_review["summary"]["accuracy_percent"] == 100.0
    assert alice_review["summary"]["score"] > 0
    for q_rev in alice_review["questions"]:
        assert q_rev["status"] == "correct"
        assert q_rev["submitted_choice"] == q_rev["correct_choice"]
        assert q_rev["submitted_text"] == q_rev["correct_text"]

    # Bob snapshot review
    bob_snap = asyncio.run(engine.snapshot(code, g_id))
    assert bob_snap.get("review") is not None
    bob_review = bob_snap["review"]
    assert bob_review["summary"]["correct_answers"] == 0
    assert bob_review["summary"]["incorrect_answers"] == 10
    assert bob_review["summary"]["unanswered_questions"] == 0
    assert bob_review["summary"]["accuracy_percent"] == 0.0
    for q_rev in bob_review["questions"]:
        assert q_rev["status"] == "incorrect"
        assert q_rev["submitted_choice"] != q_rev["correct_choice"]


def test_review_unanswered_and_timed_out():
    """Verify that timed-out questions are categorized as unanswered and not incorrect."""
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Alice"))
    code = host["state"]["code"]
    guest = asyncio.run(engine.join_room(code, "Bob"))
    h_id = host["player_id"]
    g_id = guest["player_id"]

    asyncio.run(engine.start_game(code, h_id))
    clock.advance(3.1)
    asyncio.run(engine.advance_due())

    room = engine._rooms[code]
    assert room.phase == Phase.QUESTION

    # Round 0: Alice answers correctly, Bob answers correctly
    q0 = room.questions[0]
    asyncio.run(engine.submit_answer(code, h_id, q0.answer))
    asyncio.run(engine.submit_answer(code, g_id, q0.answer))
    clock.advance(room.match_config.reveal_seconds)
    asyncio.run(engine.advance_due())

    # Remaining 9 rounds: Neither answers (time out)
    for _ in range(9):
        assert room.phase == Phase.QUESTION
        # Wait until question timer expires
        clock.advance(room.match_config.question_seconds)
        asyncio.run(engine.advance_due())
        assert room.phase == Phase.REVEAL
        clock.advance(room.match_config.reveal_seconds)
        asyncio.run(engine.advance_due())

    assert room.phase == Phase.RESULTS

    alice_snap = asyncio.run(engine.snapshot(code, h_id))
    review = alice_snap["review"]
    assert review["summary"]["total_questions"] == 10
    assert review["summary"]["correct_answers"] == 1
    assert review["summary"]["incorrect_answers"] == 0
    assert review["summary"]["unanswered_questions"] == 9
    assert review["summary"]["accuracy_percent"] == 100.0  # 1 correct out of 1 attempted

    # Verify questions 2-10 are marked unanswered
    for q_rev in review["questions"][1:]:
        assert q_rev["status"] == "unanswered"
        assert q_rev["submitted_choice"] is None
        assert q_rev["submitted_text"] is None
        assert q_rev["correct_choice"] is not None


def test_review_explanation_present_and_missing():
    """Verify handling of questions with and without explanations."""
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Alice", mode="solo_practice"))
    code = host["state"]["code"]
    h_id = host["player_id"]
    room = engine._rooms[code]

    custom_questions = [
        Question(
            id="q-exp-1",
            prompt="Which planet is the Red Planet?",
            options=("Earth", "Mars", "Jupiter", "Venus"),
            answer=1,
            category="Science",
            explanation="Mars appears reddish due to iron oxide (rust) on its surface.",
        ),
        Question(
            id="q-exp-2",
            prompt="What is 2 + 2?",
            options=("3", "4", "5", "6"),
            answer=1,
            category="Math",
            explanation=None,
        ),
    ]

    asyncio.run(engine.start_game(code, h_id))
    # Override room questions
    room.questions = custom_questions
    room.phase = Phase.RESULTS

    review = engine._build_post_game_review(room, viewer=room.players[h_id])
    assert review is not None
    assert review["questions"][0]["explanation"] == "Mars appears reddish due to iron oxide (rust) on its surface."
    assert review["questions"][1]["explanation"] is None


def test_review_missing_optional_metadata():
    """Verify graceful handling when category or other metadata is None."""
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Alice", mode="solo_practice"))
    code = host["state"]["code"]
    h_id = host["player_id"]
    room = engine._rooms[code]

    room.questions = [
        Question(
            id="q-no-meta",
            prompt="Prompt without category",
            options=("A", "B", "C", "D"),
            answer=0,
            category=None,
            explanation=None,
        )
    ]
    room.phase = Phase.RESULTS
    review = engine._build_post_game_review(room, viewer=room.players[h_id])
    assert review is not None
    assert review["questions"][0]["category"] == "General"


def test_review_backward_compatibility():
    """Verify that existing results structure remains completely intact."""
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Alice"))
    code = host["state"]["code"]
    asyncio.run(engine.join_room(code, "Bob"))
    h_id = host["player_id"]

    asyncio.run(engine.start_game(code, h_id))
    room = engine._rooms[code]
    room.round_index = 9
    room.phase = Phase.RESULTS

    snap = asyncio.run(engine.snapshot(code, h_id))
    assert len(snap["players"]) == 2
    assert "leaderboard" in snap["results"]
    assert "players" in snap
    assert "review" in snap["results"]
    assert "review" in snap


def test_review_team_battle_attribution():
    """Verify Team Battle review shows player's individual answers and team attribution."""
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Alice", mode="team_battle"))
    code = host["state"]["code"]
    bob = asyncio.run(engine.join_room(code, "Bob"))
    charlie = asyncio.run(engine.join_room(code, "Charlie"))
    dana = asyncio.run(engine.join_room(code, "Dana"))
    h_id = host["player_id"]
    b_id = bob["player_id"]

    room = engine._rooms[code]
    # Assign teams (2 per team)
    room.players[h_id].team_id = "violet"
    room.players[charlie["player_id"]].team_id = "violet"
    room.players[b_id].team_id = "cyan"
    room.players[dana["player_id"]].team_id = "cyan"

    asyncio.run(engine.start_game(code, h_id))
    clock.advance(3.1)
    asyncio.run(engine.advance_due())
    assert room.phase == Phase.QUESTION

    # Alice answers 0, Bob answers 1
    asyncio.run(engine.submit_answer(code, h_id, 0))
    asyncio.run(engine.submit_answer(code, b_id, 1))
    # Question timer expires for other players
    clock.advance(room.match_config.question_seconds)
    asyncio.run(engine.advance_due())
    assert room.phase == Phase.REVEAL

    room.phase = Phase.RESULTS
    alice_rev = engine._build_post_game_review(room, viewer=room.players[h_id])
    bob_rev = engine._build_post_game_review(room, viewer=room.players[b_id])

    assert alice_rev["questions"][0]["team_id"] == "violet"
    assert alice_rev["questions"][0]["submitted_choice"] == 0

    assert bob_rev["questions"][0]["team_id"] == "cyan"
    assert bob_rev["questions"][0]["submitted_choice"] == 1


def test_review_power_play():
    """Verify Power Play review records abilities used per round."""
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Alice", mode="power_play"))
    code = host["state"]["code"]
    guest = asyncio.run(engine.join_room(code, "Bob"))
    h_id = host["player_id"]
    g_id = guest["player_id"]

    asyncio.run(engine.start_game(code, h_id))
    room = engine._rooms[code]
    clock.advance(3.1)
    asyncio.run(engine.advance_due())
    assert room.phase == Phase.QUESTION

    # Activate 50:50 ability with valid request_id
    asyncio.run(engine.activate_ability(code, h_id, "fifty_fifty", "request-id-ability-01"))
    asyncio.run(engine.submit_answer(code, h_id, room.questions[0].answer))
    asyncio.run(engine.submit_answer(code, g_id, room.questions[0].answer))
    clock.advance(1.0)
    asyncio.run(engine.advance_due())
    assert room.phase == Phase.REVEAL

    room.phase = Phase.RESULTS
    rev = engine._build_post_game_review(room, viewer=room.players[h_id])
    assert rev["questions"][0]["power_play"]["fifty_fifty"] is True
    assert rev["questions"][0]["power_play"]["double_points"] is False
    assert rev["questions"][0]["power_play"]["shield"] is False


def test_review_bluff_master():
    """Verify Bluff Master review structure: fakes, fooled players, votes, and truth."""
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Player 0", mode="bluff_master", selection={"question_count": 10}))
    code = host["state"]["code"]
    guest = asyncio.run(engine.join_room(code, "Player 1"))
    guest2 = asyncio.run(engine.join_room(code, "Player 2"))
    h_id = host["player_id"]
    g_id = guest["player_id"]
    c_id = guest2["player_id"]

    asyncio.run(engine.start_game(code, h_id))
    clock.advance(3.1)
    asyncio.run(engine.advance_due())
    room = engine._rooms[code]
    assert room.phase == Phase.SUBMISSION

    # Submit bluffs with valid request IDs
    asyncio.run(engine.submit_bluff(code, h_id, "Alice's Fake", "request-submission-01"))
    asyncio.run(engine.submit_bluff(code, g_id, "Bob's Fake", "request-submission-02"))
    asyncio.run(engine.submit_bluff(code, c_id, "Charlie's Fake", "request-submission-03"))

    assert room.phase == Phase.VOTING

    # Alice votes for the correct answer, Bob votes for Alice's fake
    corr_opt = next(opt for opt in room.bluff_options if opt.get("is_correct"))
    alice_fake_opt = next(opt for opt in room.bluff_options if opt.get("author_id") == h_id)

    asyncio.run(engine.cast_bluff_vote(code, h_id, corr_opt["id"], "request-vote-action-01"))
    asyncio.run(engine.cast_bluff_vote(code, g_id, alice_fake_opt["id"], "request-vote-action-02"))
    asyncio.run(engine.cast_bluff_vote(code, c_id, corr_opt["id"], "request-vote-action-03"))

    assert room.phase == Phase.REVEAL

    room.phase = Phase.RESULTS
    alice_rev = engine._build_post_game_review(room, viewer=room.players[h_id])
    assert alice_rev["summary"]["mode"] == "bluff_master"
    assert alice_rev["summary"]["correct_answers"] == 1
    assert alice_rev["questions"][0]["status"] == "correct"
    assert alice_rev["questions"][0]["bluff"]["submitted_bluff"] == "Alice's Fake"
    assert "Player 1" in alice_rev["questions"][0]["bluff"]["fooled_players"]
    assert alice_rev["questions"][0]["bluff"]["voted_correct"] is True

    bob_rev = engine._build_post_game_review(room, viewer=room.players[g_id])
    assert bob_rev["summary"]["incorrect_answers"] == 1
    assert bob_rev["questions"][0]["status"] == "incorrect"
    assert bob_rev["questions"][0]["bluff"]["voted_correct"] is False


def test_review_escape_room():
    """Verify Escape Room review structure: rooms, hints, puzzle solution, and team progress."""
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Alice", mode="escape_room"))
    code = host["state"]["code"]
    asyncio.run(engine.join_room(code, "Bob"))
    h_id = host["player_id"]

    asyncio.run(engine.start_game(code, h_id))
    clock.advance(3.1)
    asyncio.run(engine.advance_due())
    room = engine._rooms[code]
    assert room.phase == Phase.ESCAPE_PUZZLE

    # Use shared hint and solve puzzle 0 with valid request IDs
    p0 = room.questions[0]
    sol0 = p0.format_payload.get("solution", "")
    asyncio.run(engine.request_escape_hint(code, h_id, "request-escape-hint-01"))
    asyncio.run(engine.submit_escape_answer(code, h_id, sol0, puzzle_index=0, request_id="request-escape-ans-01"))

    # Room advanced to puzzle index 1
    assert room.round_index == 1

    room.phase = Phase.RESULTS
    rev = engine._build_post_game_review(room, viewer=room.players[h_id])
    assert rev["summary"]["mode"] == "escape_room"
    assert rev["summary"]["score"] is None
    assert rev["summary"]["correct_answers"] == 1
    assert rev["questions"][0]["status"] == "correct"
    assert rev["questions"][0]["escape"]["hint_used"] is True
    assert (rev["questions"][0]["escape"].get("unlock_code") or rev["questions"][0]["escape"].get("solution")) == sol0
    assert rev["questions"][0]["escape"]["solved"] is True

    # Puzzle 1 was unsolved
    assert rev["questions"][1]["status"] == "unanswered"
    assert rev["questions"][1]["escape"]["solved"] is False


@pytest.mark.parametrize("mode", [
    "classic",
    "speed_challenge",
    "team_battle",
    "elimination",
    "solo_practice",
    "quiz_royale",
    "power_play",
    "bluff_master",
    "escape_room",
])
def test_all_nine_modes_generate_valid_post_game_review(mode: str):
    """Verify that all 9 game modes generate complete, valid review data at game end."""
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    config = resolve_mode(mode)
    player_count = config.min_players
    if config.even_player_count and player_count < 4:
        player_count = 4

    host = asyncio.run(engine.create_room("HostPlayer", mode=mode))
    code = host["state"]["code"]
    for i in range(1, player_count):
        asyncio.run(engine.join_room(code, f"Guest_{i}"))

    if mode == "team_battle":
        # Assign 2 players per team
        room = engine._rooms[code]
        p_list = list(room.players.values())
        p_list[0].team_id = "violet"
        p_list[1].team_id = "violet"
        p_list[2].team_id = "cyan"
        p_list[3].team_id = "cyan"

    asyncio.run(engine.start_game(code, host["player_id"]))

    room = engine._rooms[code]
    room.phase = Phase.RESULTS
    room.round_index = len(room.questions)

    snap = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert "review" in snap
    review = snap["review"]
    assert "summary" in review
    assert "questions" in review
    assert review["summary"]["total_questions"] == len(room.questions)
    assert len(review["questions"]) == len(room.questions)
    for q in review["questions"]:
        assert "number" in q
        assert "prompt" in q
        assert "status" in q
        assert q["status"] in {"correct", "incorrect", "unanswered"}
        assert "explanation" in q

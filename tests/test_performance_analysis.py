"""Unit tests for Phase 37.2 Performance Analysis & Weakness Detection."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from quizverse.engine import GameEngine, GameMode, Phase
from quizverse.intelligence.weakness_detection import WeaknessDetector
from quizverse.questions import Question


class FakeClock:
    def __init__(self) -> None:
        self.time = 1000.0

    def monotonic(self) -> float:
        return self.time

    def wall(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


def test_accuracy_calculation_formula_and_zero_attempted():
    """Verify accuracy formula (correct / attempted * 100) and safe zero division."""
    detector = WeaknessDetector()

    # 1. Zero attempted questions (all unanswered)
    review_zero = {
        "summary": {"total_questions": 5, "mode": "classic"},
        "questions": [
            {"number": 1, "category": "Science", "status": "unanswered"},
            {"number": 2, "category": "Science", "status": "unanswered"},
            {"number": 3, "category": "History", "status": "unanswered"},
            {"number": 4, "category": "History", "status": "unanswered"},
            {"number": 5, "category": "Art", "status": "unanswered"},
        ],
    }
    analysis_zero = detector.analyze_session_review(review_zero)
    assert analysis_zero is not None
    summary_zero = analysis_zero["summary"]
    assert summary_zero["total_questions"] == 5
    assert summary_zero["questions_attempted"] == 0
    assert summary_zero["correct_answers"] == 0
    assert summary_zero["incorrect_answers"] == 0
    assert summary_zero["unanswered_questions"] == 5
    assert summary_zero["accuracy_percent"] == 0.0
    assert "unanswered questions are reported separately" in summary_zero["accuracy_formula"]

    # 2. Perfect score (3/3 attempted, 2 unanswered)
    review_partial = {
        "summary": {"total_questions": 5, "mode": "classic"},
        "questions": [
            {"number": 1, "category": "Science", "status": "correct"},
            {"number": 2, "category": "Science", "status": "correct"},
            {"number": 3, "category": "Science", "status": "correct"},
            {"number": 4, "category": "Art", "status": "unanswered"},
            {"number": 5, "category": "Art", "status": "unanswered"},
        ],
    }
    analysis_partial = detector.analyze_session_review(review_partial)
    assert analysis_partial is not None
    s = analysis_partial["summary"]
    assert s["questions_attempted"] == 3
    assert s["correct_answers"] == 3
    assert s["unanswered_questions"] == 2
    # Accuracy should be 3/3 * 100 = 100.0%, NOT 3/5 (60%)
    assert s["accuracy_percent"] == 100.0


def test_correct_incorrect_and_unanswered_breakdown():
    """Verify that timeouts/unanswered are not marked as incorrect answers."""
    detector = WeaknessDetector()
    review = {
        "summary": {"total_questions": 10, "mode": "solo_practice"},
        "questions": [
            {"number": 1, "category": "Science", "status": "correct"},
            {"number": 2, "category": "Science", "status": "correct"},
            {"number": 3, "category": "History", "status": "incorrect"},
            {"number": 4, "category": "History", "status": "incorrect"},
            {"number": 5, "category": "Geography", "status": "unanswered"},  # Timeout
            {"number": 6, "category": "Geography", "status": "unanswered"},  # Timeout
            {"number": 7, "category": "Geography", "status": "unanswered"},  # Timeout
            {"number": 8, "category": "Art", "status": "correct"},
            {"number": 9, "category": "Art", "status": "incorrect"},
            {"number": 10, "category": "Art", "status": "unanswered"},
        ],
    }
    analysis = detector.analyze_session_review(review)
    assert analysis is not None
    summary = analysis["summary"]
    assert summary["total_questions"] == 10
    assert summary["correct_answers"] == 3
    assert summary["incorrect_answers"] == 3
    assert summary["unanswered_questions"] == 4
    assert summary["questions_attempted"] == 6
    # 3 / 6 = 50.0%
    assert summary["accuracy_percent"] == 50.0


def test_topic_grouping_and_classification_thresholds():
    """Verify topic grouping and transparent thresholds: Strong, Improving, Needs Practice, Insufficient Data."""
    detector = WeaknessDetector()
    review = {
        "summary": {"total_questions": 10, "mode": "classic"},
        "questions": [
            # Science: 3/4 correct = 75.0% -> Strong (>= 75%)
            {"number": 1, "category": "Science", "status": "correct"},
            {"number": 2, "category": "Science", "status": "correct"},
            {"number": 3, "category": "Science", "status": "correct"},
            {"number": 4, "category": "Science", "status": "incorrect"},
            # Geography: 1/2 correct = 50.0% -> Improving (>= 50% and < 75%)
            {"number": 5, "category": "Geography", "status": "correct"},
            {"number": 6, "category": "Geography", "status": "incorrect"},
            # History: 0/2 correct = 0.0% -> Needs Practice (< 50%)
            {"number": 7, "category": "History", "status": "incorrect"},
            {"number": 8, "category": "History", "status": "incorrect"},
            # Space: 1/1 correct -> Insufficient Data (< 2 attempted)
            {"number": 9, "category": "Space", "status": "correct"},
            # Art: 0/0 attempted (1 unanswered) -> Insufficient Data
            {"number": 10, "category": "Art", "status": "unanswered"},
        ],
    }
    analysis = detector.analyze_session_review(review)
    assert analysis is not None

    topics_by_name = {t["topic_name"]: t for t in analysis["topics"]}
    assert set(topics_by_name.keys()) == {"Art", "Geography", "History", "Science", "Space"}

    # Science
    sci = topics_by_name["Science"]
    assert sci["questions_attempted"] == 4
    assert sci["correct_answers"] == 3
    assert sci["accuracy_percent"] == 75.0
    assert sci["classification"] == "Strong"
    assert "75.0% accuracy (3/4 correct)" in sci["reason"]

    # Geography
    geo = topics_by_name["Geography"]
    assert geo["questions_attempted"] == 2
    assert geo["correct_answers"] == 1
    assert geo["accuracy_percent"] == 50.0
    assert geo["classification"] == "Improving"
    assert "50.0% accuracy (1/2 correct)" in geo["reason"]

    # History
    hist = topics_by_name["History"]
    assert hist["questions_attempted"] == 2
    assert hist["correct_answers"] == 0
    assert hist["accuracy_percent"] == 0.0
    assert hist["classification"] == "Needs Practice"
    assert "0.0% accuracy" in hist["reason"]

    # Space (< 2 attempted)
    sp = topics_by_name["Space"]
    assert sp["questions_attempted"] == 1
    assert sp["classification"] == "Insufficient Data"
    assert "minimum 2 needed" in sp["reason"]

    # Art (0 attempted)
    art = topics_by_name["Art"]
    assert art["questions_attempted"] == 0
    assert art["classification"] == "Insufficient Data"
    assert "No questions attempted" in art["reason"]

    # Check clusters
    assert [t["topic_name"] for t in analysis["strengths"]] == ["Science"]
    assert [t["topic_name"] for t in analysis["weaknesses"]] == ["History"]
    assert [t["topic_name"] for t in analysis["improving"]] == ["Geography"]
    assert {t["topic_name"] for t in analysis["insufficient_data"]} == {"Space", "Art"}


def test_insufficient_topic_metadata_fallback():
    """Verify fallback when category is None or empty string."""
    detector = WeaknessDetector()
    review = {
        "summary": {"total_questions": 2, "mode": "classic"},
        "questions": [
            {"number": 1, "category": None, "status": "correct"},
            {"number": 2, "category": "", "status": "correct"},
        ],
    }
    analysis = detector.analyze_session_review(review)
    assert analysis is not None
    assert len(analysis["topics"]) == 1
    topic = analysis["topics"][0]
    assert topic["topic_name"] == "Topic information unavailable"
    assert topic["questions_attempted"] == 2
    assert topic["accuracy_percent"] == 100.0
    assert topic["classification"] == "Strong"


def test_repeated_mistakes_detection():
    """Verify identification of repeated mistakes (2+ incorrect in a topic) with question prompts."""
    detector = WeaknessDetector()
    review = {
        "summary": {"total_questions": 4, "mode": "classic"},
        "questions": [
            {"number": 1, "category": "History", "prompt": "Who signed the Magna Carta?", "status": "incorrect"},
            {"number": 2, "category": "History", "prompt": "When did the French Revolution start?", "status": "incorrect"},
            {"number": 3, "category": "Science", "prompt": "Boiling point of water?", "status": "correct"},
            {"number": 4, "category": "Science", "prompt": "Speed of light?", "status": "incorrect"},
        ],
    }
    analysis = detector.analyze_session_review(review)
    assert analysis is not None
    mistakes = analysis["repeated_mistakes"]
    assert len(mistakes) == 1
    hist_mistake = mistakes[0]
    assert hist_mistake["topic_name"] == "History"
    assert hist_mistake["mistake_count"] == 2
    assert len(hist_mistake["missed_questions"]) == 2
    assert hist_mistake["missed_questions"][0]["prompt"] == "Who signed the Magna Carta?"
    assert hist_mistake["missed_questions"][1]["prompt"] == "When did the French Revolution start?"


def test_evidence_based_recommendations():
    """Verify factual and evidence-based recommendation generation."""
    detector = WeaknessDetector()
    review = {
        "summary": {"total_questions": 4, "mode": "classic"},
        "questions": [
            {"number": 1, "category": "History", "prompt": "Q1", "status": "incorrect"},
            {"number": 2, "category": "History", "prompt": "Q2", "status": "incorrect"},
            {"number": 3, "category": "Science", "prompt": "Q3", "status": "correct"},
            {"number": 4, "category": "Science", "prompt": "Q4", "status": "correct"},
        ],
    }
    analysis = detector.analyze_session_review(review)
    assert analysis is not None
    recs = analysis["recommendations"]
    assert any("Prioritize practice in History: 0/2 correct (0.0% accuracy)" in r for r in recs)
    assert any("Review foundational concepts in History (2 missed questions" in r for r in recs)
    assert any("Maintain mastery in Science (100.0% accuracy across 2 questions)" in r for r in recs)


def test_team_battle_attribution():
    """Verify that individual metrics are distinguished from team totals in Team Battle."""
    detector = WeaknessDetector()
    review = {
        "summary": {"total_questions": 4, "mode": "team_battle"},
        "questions": [
            {"number": 1, "category": "Science", "status": "correct"},
            {"number": 2, "category": "Science", "status": "correct"},
            {"number": 3, "category": "History", "status": "correct"},
            {"number": 4, "category": "History", "status": "incorrect"},
        ],
    }
    extra_context = {
        "team_id": "violet",
        "player_score": 3000,
        "team_score": 7500,
        "opposing_team_name": "Team Cyan",
        "opposing_team_score": 6000,
        "team_outcome": "victory",
    }
    analysis = detector.analyze_session_review(review, mode="team_battle", extra_context=extra_context)
    assert analysis is not None
    assert "team_details" in analysis
    td = analysis["team_details"]
    assert td["team_name"] == "Team Violet"
    assert td["player_score"] == 3000
    assert td["team_score"] == 7500
    assert td["opposing_team_score"] == 6000
    assert td["team_outcome"] == "victory"
    assert "Individual metrics reflect your personal answers" in td["individual_note"]
    # Individual accuracy is 3/4 = 75.0%
    assert analysis["summary"]["accuracy_percent"] == 75.0


def test_elimination_mode_handling():
    """Verify that eliminated players have their rounds survived tracked and excluded properly."""
    detector = WeaknessDetector()
    review = {
        "summary": {"total_questions": 5, "mode": "elimination"},
        "questions": [
            {"number": 1, "category": "Science", "status": "correct"},
            {"number": 2, "category": "Science", "status": "correct"},
            {"number": 3, "category": "History", "status": "incorrect"},  # Eliminated here
            {"number": 4, "category": "History", "status": "unanswered", "eliminated_prior": True},
            {"number": 5, "category": "Art", "status": "unanswered", "eliminated_prior": True},
        ],
    }
    extra_context = {
        "eliminated": True,
        "eliminated_round": 3,
        "rounds_survived": 2,
    }
    analysis = detector.analyze_session_review(review, mode="elimination", extra_context=extra_context)
    assert analysis is not None
    assert analysis["summary"]["completion_status"] == "Eliminated in Round 3"
    # Attempted = 3 questions (2 correct + 1 incorrect). Questions 4 and 5 were post-elimination timeouts.
    assert analysis["summary"]["questions_attempted"] == 3
    assert analysis["summary"]["correct_answers"] == 2
    assert analysis["summary"]["incorrect_answers"] == 1
    assert analysis["summary"]["unanswered_questions"] == 2
    assert analysis["summary"]["accuracy_percent"] == 66.7
    assert "elimination_details" in analysis
    ed = analysis["elimination_details"]
    assert ed["eliminated"] is True
    assert ed["eliminated_round"] == 3
    assert ed["rounds_survived"] == 2


def test_bluff_master_honest_limitations():
    """Verify that Bluff Master presents deception/bluff metrics and honest limitations."""
    detector = WeaknessDetector()
    review = {
        "summary": {
            "total_questions": 5,
            "mode": "bluff_master",
            "score": 4500,
            "fooled_players_count": 6,
            "correct_answers": 3,
            "incorrect_answers": 2,
            "unanswered_questions": 0,
        },
        "questions": [
            {"number": 1, "prompt": "Bluff Q1", "status": "correct", "submitted_text": "Fake 1"},
            {"number": 2, "prompt": "Bluff Q2", "status": "correct", "submitted_text": "Fake 2"},
            {"number": 3, "prompt": "Bluff Q3", "status": "correct", "submitted_text": "Fake 3"},
            {"number": 4, "prompt": "Bluff Q4", "status": "incorrect", "submitted_text": "Fake 4"},
            {"number": 5, "prompt": "Bluff Q5", "status": "incorrect", "submitted_text": "Fake 5"},
        ],
    }
    extra_context = {
        "bluffs_submitted": 5,
        "fooled_players_count": 6,
        "correct_truths_voted": 3,
        "fooled_votes_cast": 2,
        "unanswered_votes": 0,
        "player_score": 4500,
    }
    analysis = detector.analyze_session_review(review, mode="bluff_master", extra_context=extra_context)
    assert analysis is not None
    assert analysis["summary"]["mode"] == "bluff_master"
    assert analysis["summary"]["accuracy_percent"] is None  # Multiple choice accuracy is N/A
    assert "social deception and bluff voting" in analysis["summary"]["accuracy_formula"]
    assert "bluff_performance" in analysis
    bp = analysis["bluff_performance"]
    assert bp["bluffs_submitted"] == 5
    assert bp["fooled_players_count"] == 6
    assert bp["correct_truths_voted"] == 3
    assert bp["fooled_votes_cast"] == 2
    assert "Bluff Master evaluates deception and social deduction" in analysis["mode_notes"]
    assert len(analysis["topics"]) == 0  # Not applicable to bluffing


def test_escape_room_honest_limitations():
    """Verify that Escape Room presents chamber and hint milestones and honest limitations."""
    detector = WeaknessDetector()
    review = {
        "summary": {"total_questions": 10, "mode": "escape_room"},
        "questions": [
            {"number": i + 1, "prompt": f"Puzzle {i + 1}", "status": "correct" if i < 8 else "unanswered"}
            for i in range(10)
        ],
    }
    extra_context = {
        "hints_used": 2,
        "hints_remaining": 1,
        "elapsed_seconds": 240,
        "outcome": "victory",
    }
    analysis = detector.analyze_session_review(review, mode="escape_room", extra_context=extra_context)
    assert analysis is not None
    assert analysis["summary"]["mode"] == "escape_room"
    assert analysis["summary"]["accuracy_percent"] is None
    assert "cooperative room escape puzzles" in analysis["summary"]["accuracy_formula"]
    assert "escape_progress" in analysis
    ep = analysis["escape_progress"]
    assert ep["solved_puzzles"] == 8
    assert ep["completed_rooms"] == 4
    assert ep["total_rooms"] == 5
    assert ep["hints_used"] == 2
    assert ep["hints_remaining"] == 1
    assert ep["elapsed_seconds"] == 240
    assert ep["outcome"] == "victory"
    assert "cooperative cipher-solving challenge" in analysis["mode_notes"]
    assert len(analysis["topics"]) == 0


def test_historical_context_reporting():
    """Verify historical progress availability notice for guest vs profile."""
    detector = WeaknessDetector()
    review = {
        "summary": {"total_questions": 2, "mode": "classic"},
        "questions": [
            {"number": 1, "category": "Science", "status": "correct"},
            {"number": 2, "category": "Science", "status": "correct"},
        ],
    }

    # Guest session (no profile)
    analysis_guest = detector.analyze_session_review(review, extra_context={"has_profile": False})
    assert analysis_guest is not None
    hc_guest = analysis_guest["historical_context"]
    assert hc_guest["is_available"] is False
    assert "Historical trend tracking requires an authenticated profile" in hc_guest["note"]

    # Authenticated profile with 3 matches
    analysis_profile = detector.analyze_session_review(
        review, extra_context={"has_profile": True, "historical_matches_count": 3}
    )
    assert analysis_profile is not None
    assert analysis_profile["historical_context"]["is_available"] is True


def test_access_control_no_analysis_before_results():
    """Verify performance analysis is None and inaccessible before Phase.RESULTS."""
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Alice"))
    code = host["state"]["code"]
    h_id = host["player_id"]
    asyncio.run(engine.join_room(code, "Bob"))

    # Phase: WAITING
    snap = asyncio.run(engine.snapshot(code, h_id))
    assert snap.get("performance_analysis") is None
    assert "performance_analysis" not in (snap.get("results") or {})

    # Start game -> COUNTDOWN
    asyncio.run(engine.start_game(code, h_id))
    snap = asyncio.run(engine.snapshot(code, h_id))
    assert snap["phase"] == Phase.COUNTDOWN
    assert snap.get("performance_analysis") is None

    # Countdown -> QUESTION
    clock.advance(3.1)
    asyncio.run(engine.advance_due())
    snap = asyncio.run(engine.snapshot(code, h_id))
    assert snap["phase"] == Phase.QUESTION
    assert snap.get("performance_analysis") is None


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
def test_all_nine_modes_generate_valid_performance_analysis(mode: str):
    """Verify that GameEngine generates a valid performance_analysis snapshot across all 9 modes."""
    from quizverse.modes import resolve_mode
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
    assert snap["phase"] == Phase.RESULTS
    assert snap.get("performance_analysis") is not None
    analysis = snap["performance_analysis"]
    assert analysis["summary"]["mode"] == mode
    assert "questions_attempted" in analysis["summary"]
    assert "accuracy_percent" in analysis["summary"]
    assert "topics" in analysis
    assert "strengths" in analysis
    assert "weaknesses" in analysis
    assert "recommendations" in analysis


def test_backward_compatibility_empty_or_none():
    """Verify graceful handling when review data is None or empty."""
    detector = WeaknessDetector()
    assert detector.analyze_session_review(None) is None
    assert detector.analyze_session_review({}) is None
    assert detector.analyze_session_review("invalid") is None  # type: ignore[arg-type]

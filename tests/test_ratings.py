"""Tests for competitive Elo ratings, eligibility, multiplayer calculations, APIs and security."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import math
import os
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    CompetitiveMatch,
    MatchParticipant,
    PlayerProfile,
    PlayerRating,
    RatingHistory,
)
from quizverse.persistence.player_identity import (
    MatchHistoryService,
    MatchResultError,
    PlayerIdentityService,
)
from quizverse.persistence.ratings import (
    DEFAULT_CONFIG,
    ELIGIBLE_MODES,
    EXCLUDED_MODES,
    ParticipantRatingAdjustment,
    ParticipantRatingState,
    RatingConfig,
    RatingEligibilityChecker,
    RatingService,
    calculate_multiplayer_elo,
)
from quizverse.realtime import create_app


# =============================================================================
# 1. MATHEMATICAL ELO UNIT TESTS
# =============================================================================

def test_expected_score_formula_and_symmetry():
    # Equal ratings -> exactly 0.5 expected score
    diff_zero = (1000 - 1000) / 400.0
    e_equal = 1.0 / (1.0 + math.pow(10.0, diff_zero))
    assert math.isclose(e_equal, 0.5)

    # 400 points difference -> 10 to 1 odds (~0.909 vs ~0.091)
    diff_400 = (800 - 1200) / 400.0
    e_favored = 1.0 / (1.0 + math.pow(10.0, diff_400))
    e_underdog = 1.0 / (1.0 + math.pow(10.0, -diff_400))
    assert math.isclose(e_favored, 10.0 / 11.0, rel_tol=1e-5)
    assert math.isclose(e_underdog, 1.0 / 11.0, rel_tol=1e-5)
    assert math.isclose(e_favored + e_underdog, 1.0)


def test_two_player_win_loss_is_zero_sum():
    p1 = ParticipantRatingState(player_id="p1", prior_rating=1000, placement=1, outcome="win")
    p2 = ParticipantRatingState(player_id="p2", prior_rating=1000, placement=2, outcome="loss")

    results = calculate_multiplayer_elo([p1, p2], DEFAULT_CONFIG)
    assert len(results) == 2
    r1 = next(r for r in results if r.player_id == "p1")
    r2 = next(r for r in results if r.player_id == "p2")

    # With K=32 and equal ratings: Winner +16, Loser -16
    assert r1.rating_delta == 16
    assert r1.new_rating == 1016
    assert r1.actual_score == 1.0
    assert r1.expected_score == 0.5

    assert r2.rating_delta == -16
    assert r2.new_rating == 984
    assert r2.actual_score == 0.0
    assert r2.expected_score == 0.5

    assert r1.rating_delta + r2.rating_delta == 0


def test_two_player_draw_is_zero_delta():
    p1 = ParticipantRatingState(player_id="p1", prior_rating=1000, placement=1, outcome="draw")
    p2 = ParticipantRatingState(player_id="p2", prior_rating=1000, placement=1, outcome="draw")

    results = calculate_multiplayer_elo([p1, p2], DEFAULT_CONFIG)
    r1, r2 = results[0], results[1]
    assert r1.rating_delta == 0
    assert r2.rating_delta == 0
    assert r1.new_rating == 1000
    assert r2.new_rating == 1000
    assert r1.rating_delta + r2.rating_delta == 0


def test_zero_rating_floor_preserves_zero_sum_and_history_arithmetic():
    p1 = ParticipantRatingState(player_id="floor-winner", prior_rating=0, placement=1, outcome="win")
    p2 = ParticipantRatingState(player_id="floor-loser", prior_rating=0, placement=2, outcome="loss")

    results = calculate_multiplayer_elo([p1, p2], DEFAULT_CONFIG)
    assert all(result.new_rating >= 0 for result in results)
    assert sum(result.rating_delta for result in results) == 0
    assert all(result.new_rating - result.prior_rating == result.rating_delta for result in results)
    assert all(result.new_rating == 0 for result in results)


def test_multiplayer_first_and_last_place_4_players_zero_sum():
    # 4 equal rating players: placements 1, 2, 3, 4
    players = [
        ParticipantRatingState(player_id=f"p{i}", prior_rating=1000, placement=i, outcome="win" if i == 1 else "loss")
        for i in range(1, 5)
    ]
    results = calculate_multiplayer_elo(players, DEFAULT_CONFIG)
    assert len(results) == 4

    p1_res = next(r for r in results if r.player_id == "p1")
    p4_res = next(r for r in results if r.player_id == "p4")

    # Place 1: S_avg = 3/3 = 1.0, E_avg = 0.5 -> delta = 32*(0.5) = +16
    assert p1_res.rating_delta == 16
    # Place 4: S_avg = 0/3 = 0.0, E_avg = 0.5 -> delta = 32*(-0.5) = -16
    assert p4_res.rating_delta == -16

    # Verify zero-sum
    assert sum(r.rating_delta for r in results) == 0


def test_multiplayer_tied_placements_receive_half_actual_score():
    # 3 players: p1 wins (place 1), p2 and p3 tie for place 2
    players = [
        ParticipantRatingState(player_id="p1", prior_rating=1000, placement=1, outcome="win"),
        ParticipantRatingState(player_id="p2", prior_rating=1000, placement=2, outcome="loss"),
        ParticipantRatingState(player_id="p3", prior_rating=1000, placement=2, outcome="loss"),
    ]
    results = calculate_multiplayer_elo(players, DEFAULT_CONFIG)

    r1 = next(r for r in results if r.player_id == "p1")
    r2 = next(r for r in results if r.player_id == "p2")
    r3 = next(r for r in results if r.player_id == "p3")

    # P1 beat both: S_avg = 2/2 = 1.0, delta = +16
    assert r1.rating_delta == 16
    # P2 lost to P1 (0), drew with P3 (0.5): S_avg = 0.5/2 = 0.25, E_avg = 0.5 -> delta = 32*(0.25 - 0.5) = -8
    assert r2.rating_delta == -8
    assert r3.rating_delta == -8

    assert sum(r.rating_delta for r in results) == 0


def test_unequal_ratings_and_upset_win():
    # Underdog (800) beats Favorite (1200)
    p_underdog = ParticipantRatingState(player_id="dog", prior_rating=800, placement=1, outcome="win")
    p_favorite = ParticipantRatingState(player_id="fav", prior_rating=1200, placement=2, outcome="loss")

    results = calculate_multiplayer_elo([p_underdog, p_favorite], DEFAULT_CONFIG)
    r_dog = next(r for r in results if r.player_id == "dog")
    r_fav = next(r for r in results if r.player_id == "fav")

    # Underdog gain must be substantial (> +25 with K=32)
    assert r_dog.rating_delta >= 28
    assert math.isclose(r_dog.expected_score, 1.0 / 11.0, rel_tol=1e-6)
    assert math.isclose(r_fav.expected_score, 10.0 / 11.0, rel_tol=1e-6)
    assert r_fav.rating_delta == -r_dog.rating_delta
    assert r_dog.rating_delta + r_fav.rating_delta == 0


def test_k_factor_configuration_and_algorithm_version():
    custom_config = RatingConfig(
        starting_rating=1200,
        default_k_factor=16,
        provisional_threshold=5,
        algorithm_version="elo_custom_v2",
    )
    p1 = ParticipantRatingState(player_id="p1", prior_rating=1200, placement=1, outcome="win")
    p2 = ParticipantRatingState(player_id="p2", prior_rating=1200, placement=2, outcome="loss")

    results = calculate_multiplayer_elo([p1, p2], custom_config)
    r1 = next(r for r in results if r.player_id == "p1")
    # With K=16, delta is +8 instead of +16
    assert r1.rating_delta == 8
    assert r1.new_rating == 1208
    assert r1.algorithm_version == "elo_custom_v2"


def test_deterministic_rounding_across_various_roster_sizes():
    # Test rosters of sizes 3, 5, 7, 8 with various ratings
    for roster_size in [3, 5, 6, 7, 8]:
        players = [
            ParticipantRatingState(
                player_id=f"player_{i:02d}",
                prior_rating=900 + (i * 37) % 300,
                placement=i,
                outcome="win" if i == 1 else "loss",
            )
            for i in range(1, roster_size + 1)
        ]
        results = calculate_multiplayer_elo(players, DEFAULT_CONFIG)
        total_delta = sum(r.rating_delta for r in results)
        assert total_delta == 0, f"Failed zero-sum for roster size {roster_size}"


# =============================================================================
# 2. ELIGIBILITY TESTS
# =============================================================================

def test_eligibility_accepts_all_six_eligible_modes():
    for mode in ELIGIBLE_MODES:
        payload = {
            "status": "completed",
            "mode": mode,
            "result_kind": "individual",
            "participants": [
                {"player_id": "a" * 32, "placement": 1},
                {"player_id": "b" * 32, "placement": 2},
            ],
        }
        eligible, reason = RatingEligibilityChecker.check_eligibility(payload)
        assert eligible is True, f"Mode {mode} should be eligible, got {reason}"
        assert reason is None


def test_eligibility_rejects_excluded_modes():
    for mode in EXCLUDED_MODES:
        payload = {
            "status": "completed",
            "mode": mode,
            "result_kind": "team" if mode == "team_battle" else "cooperative" if mode == "escape_room" else "practice",
            "participants": [
                {"player_id": "a" * 32, "placement": 1},
                {"player_id": "b" * 32, "placement": 2},
            ],
        }
        eligible, reason = RatingEligibilityChecker.check_eligibility(payload)
        assert eligible is False
        assert reason == f"mode_excluded:{mode}"


def test_eligibility_rejects_anonymous_unauthenticated_matches():
    # If any participant is an unauthenticated guest (player_id is None)
    payload = {
        "status": "completed",
        "mode": "classic",
        "result_kind": "individual",
        "participants": [
            {"player_id": "a" * 32, "placement": 1},
            {"player_id": None, "placement": 2},
        ],
    }
    eligible, reason = RatingEligibilityChecker.check_eligibility(payload)
    assert eligible is False
    assert reason == "unauthenticated_participants"


def test_eligibility_rejects_incomplete_matches():
    payload = {
        "status": "in_progress",
        "mode": "classic",
        "result_kind": "individual",
        "participants": [
            {"player_id": "a" * 32, "placement": 1},
            {"player_id": "b" * 32, "placement": 2},
        ],
    }
    eligible, reason = RatingEligibilityChecker.check_eligibility(payload)
    assert eligible is False
    assert reason == "match_not_completed"


def test_eligibility_rejects_single_player_match():
    payload = {
        "status": "completed",
        "mode": "classic",
        "result_kind": "individual",
        "participants": [
            {"player_id": "a" * 32, "placement": 1},
        ],
    }
    eligible, reason = RatingEligibilityChecker.check_eligibility(payload)
    assert eligible is False
    assert reason == "insufficient_participants"


def test_eligibility_rejects_duplicate_player_identities():
    payload = {
        "status": "completed",
        "mode": "classic",
        "result_kind": "individual",
        "participants": [
            {"player_id": "a" * 32, "placement": 1},
            {"player_id": "a" * 32, "placement": 2},
        ],
    }
    eligible, reason = RatingEligibilityChecker.check_eligibility(payload)
    assert eligible is False
    assert reason == "duplicate_player_identity"


def test_eligibility_rejects_invalid_placements():
    for bad_placement in [None, 0, -1, "first"]:
        payload = {
            "status": "completed",
            "mode": "classic",
            "result_kind": "individual",
            "participants": [
                {"player_id": "a" * 32, "placement": 1},
                {"player_id": "b" * 32, "placement": bad_placement},
            ],
        }
        eligible, reason = RatingEligibilityChecker.check_eligibility(payload)
        assert eligible is False
        assert reason == "invalid_placements"


# =============================================================================
# 3. FIXTURES AND DATABASE INTEGRATION TESTS
# =============================================================================

@pytest.fixture
def ratings_database(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'ratings-test.sqlite3').as_posix()}"
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
def ratings_app(ratings_database: Database):
    with TestClient(create_app(database=ratings_database)) as client:
        yield client, ratings_database


def _make_match_result(
    engine_match_id: str,
    mode: str,
    participants: list[dict],
    result_kind: str = "individual",
    status: str = "completed",
) -> dict:
    now = datetime.now(UTC)
    return {
        "source": "game_engine",
        "engine_match_id": engine_match_id,
        "mode": mode,
        "status": status,
        "result_kind": result_kind,
        "rules_version": "v1.0",
        "started_at": now - timedelta(minutes=5),
        "completed_at": now,
        "question_set_hash": "a" * 64,
        "participants": participants,
        "teams": [],
        "result_context": {"outcome": "victory"},
    }


def test_ratings_atomic_update_global_and_mode_in_one_transaction(ratings_database: Database):
    identities = PlayerIdentityService(ratings_database)
    p1 = identities.create("Player Alpha")
    p2 = identities.create("Player Beta")

    history = MatchHistoryService(ratings_database)
    result = _make_match_result(
        engine_match_id="1" * 32,
        mode="classic",
        participants=[
            {
                "seat_index": 0, "player_id": p1.player_id, "placement": 1,
                "outcome": "win", "final_score": 100, "correct_count": 5, "answer_count": 5,
                "disconnect_count": 0, "reconnect_expired": False, "forfeited": False,
            },
            {
                "seat_index": 1, "player_id": p2.player_id, "placement": 2,
                "outcome": "loss", "final_score": 50, "correct_count": 2, "answer_count": 5,
                "disconnect_count": 0, "reconnect_expired": False, "forfeited": False,
            },
        ],
    )

    persisted = history.persist_completed(result)
    assert persisted is True

    # Verify both global and mode ratings were created
    rating_service = history.rating_service
    p1_summary = rating_service.player_ratings_summary(p1.player_id)
    assert p1_summary["global_rating"] is not None
    assert p1_summary["global_rating"]["rating"] == 1016
    assert p1_summary["global_rating"]["rated_match_count"] == 1
    assert p1_summary["global_rating"]["wins"] == 1
    assert p1_summary["global_rating"]["losses"] == 0
    assert p1_summary["global_rating"]["is_provisional"] is True

    # Mode rating exists for classic
    assert "classic" in p1_summary["mode_ratings"]
    assert p1_summary["mode_ratings"]["classic"]["rating"] == 1016
    assert p1_summary["mode_ratings"]["classic"]["rated_match_count"] == 1

    # Unrelated modes are NOT updated
    assert "speed_challenge" not in p1_summary["mode_ratings"]

    # Verify history entries: 2 entries for each player (1 global, 1 mode:classic)
    p1_history = rating_service.player_rating_history(p1.player_id)
    assert p1_history["total"] == 2
    scopes = {h["scope"] for h in p1_history["history"]}
    assert scopes == {"global", "mode:classic"}
    assert all(h["rating_delta"] == 16 for h in p1_history["history"])


def test_match_and_rating_writes_roll_back_together_on_rating_failure(ratings_database: Database):
    identities = PlayerIdentityService(ratings_database)
    p1 = identities.create("Rollback One")
    p2 = identities.create("Rollback Two")

    class FailingRatingService(RatingService):
        def apply_match_ratings(self, session, match, participants):
            super().apply_match_ratings(session, match, participants)
            raise RuntimeError("simulated rating transaction failure")

    history = MatchHistoryService(ratings_database, FailingRatingService(ratings_database))
    result = _make_match_result(
        engine_match_id="a" * 32,
        mode="classic",
        participants=[
            {"seat_index": 0, "player_id": p1.player_id, "placement": 1, "outcome": "win", "final_score": 100, "correct_count": 5, "answer_count": 5, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 1, "player_id": p2.player_id, "placement": 2, "outcome": "loss", "final_score": 50, "correct_count": 2, "answer_count": 5, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
        ],
    )

    with pytest.raises(RuntimeError, match="simulated rating transaction failure"):
        history.persist_completed(result)

    with ratings_database.session() as session:
        assert session.query(CompetitiveMatch).count() == 0
        assert session.query(MatchParticipant).count() == 0
        assert session.query(PlayerRating).count() == 0
        assert session.query(RatingHistory).count() == 0


def test_idempotent_duplicate_match_persistence_does_not_double_rate(ratings_database: Database):
    identities = PlayerIdentityService(ratings_database)
    p1 = identities.create("Player One")
    p2 = identities.create("Player Two")

    history = MatchHistoryService(ratings_database)
    result = _make_match_result(
        engine_match_id="2" * 32,
        mode="speed_challenge",
        participants=[
            {
                "seat_index": 0, "player_id": p1.player_id, "placement": 1,
                "outcome": "win", "final_score": 100, "correct_count": 5, "answer_count": 5,
                "disconnect_count": 0, "reconnect_expired": False, "forfeited": False,
            },
            {
                "seat_index": 1, "player_id": p2.player_id, "placement": 2,
                "outcome": "loss", "final_score": 50, "correct_count": 2, "answer_count": 5,
                "disconnect_count": 0, "reconnect_expired": False, "forfeited": False,
            },
        ],
    )

    assert history.persist_completed(result) is True
    # Second call with exact same result data: idempotent return False
    assert history.persist_completed(result) is False

    # Rating must still be 1016, not 1032!
    summary = history.rating_service.player_ratings_summary(p1.player_id)
    assert summary["global_rating"]["rating"] == 1016
    assert summary["global_rating"]["rated_match_count"] == 1


def test_conflicting_retry_is_rejected_with_error(ratings_database: Database):
    identities = PlayerIdentityService(ratings_database)
    p1 = identities.create("Player One")
    p2 = identities.create("Player Two")

    history = MatchHistoryService(ratings_database)
    result1 = _make_match_result(
        engine_match_id="3" * 32,
        mode="classic",
        participants=[
            {
                "seat_index": 0, "player_id": p1.player_id, "placement": 1,
                "outcome": "win", "final_score": 100, "correct_count": 5, "answer_count": 5,
                "disconnect_count": 0, "reconnect_expired": False, "forfeited": False,
            },
            {
                "seat_index": 1, "player_id": p2.player_id, "placement": 2,
                "outcome": "loss", "final_score": 50, "correct_count": 2, "answer_count": 5,
                "disconnect_count": 0, "reconnect_expired": False, "forfeited": False,
            },
        ],
    )
    assert history.persist_completed(result1) is True

    # Conflicting result with flipped placement
    result2 = _make_match_result(
        engine_match_id="3" * 32,
        mode="classic",
        participants=[
            {
                "seat_index": 0, "player_id": p1.player_id, "placement": 2,
                "outcome": "loss", "final_score": 50, "correct_count": 2, "answer_count": 5,
                "disconnect_count": 0, "reconnect_expired": False, "forfeited": False,
            },
            {
                "seat_index": 1, "player_id": p2.player_id, "placement": 1,
                "outcome": "win", "final_score": 100, "correct_count": 5, "answer_count": 5,
                "disconnect_count": 0, "reconnect_expired": False, "forfeited": False,
            },
        ],
    )
    with pytest.raises(MatchResultError, match="conflicting result data"):
        history.persist_completed(result2)


def test_reconcile_unrated_matches_recovers_missing_ratings(ratings_database: Database):
    identities = PlayerIdentityService(ratings_database)
    p1 = identities.create("Reconcile P1")
    p2 = identities.create("Reconcile P2")

    # Directly insert an unrated completed match
    with ratings_database.sessions.begin() as session:
        match_id = "4" * 32
        match = CompetitiveMatch(
            id=match_id,
            engine_match_id="4" * 32,
            mode="elimination",
            status="completed",
            result_kind="individual",
            rules_version="v1",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            question_set_hash="e" * 64,
            result_digest="f" * 64,
            result_context={},
            is_rated=False,
        )
        session.add(match)
        session.flush()
        session.add_all([
            MatchParticipant(
                id="p_part_1", match_id=match_id, player_id=p1.player_id,
                seat_index=0, final_score=80, correct_count=4, answer_count=4,
                placement=1, outcome="win",
            ),
            MatchParticipant(
                id="p_part_2", match_id=match_id, player_id=p2.player_id,
                seat_index=1, final_score=40, correct_count=2, answer_count=4,
                placement=2, outcome="loss",
            ),
        ])

    service = RatingService(ratings_database)
    unrated = service.detect_unrated_eligible_matches()
    assert match_id in unrated

    reconciled = service.reconcile_unrated_matches()
    assert match_id in reconciled

    # Check that ratings were applied
    p1_summary = service.player_ratings_summary(p1.player_id)
    assert p1_summary["global_rating"]["rating"] == 1016
    assert "elimination" in p1_summary["mode_ratings"]

    # Re-running detection shows 0 unrated
    assert len(service.detect_unrated_eligible_matches()) == 0


def test_app_startup_reconciles_existing_stage_81_match_history(ratings_database: Database):
    identities = PlayerIdentityService(ratings_database)
    p1 = identities.create("Existing Match One")
    p2 = identities.create("Existing Match Two")
    match_id = "b" * 32
    with ratings_database.sessions.begin() as session:
        session.add(CompetitiveMatch(
            id=match_id,
            engine_match_id=match_id,
            mode="classic",
            status="completed",
            result_kind="individual",
            rules_version="v1",
            started_at=datetime.now(UTC) - timedelta(minutes=1),
            completed_at=datetime.now(UTC),
            question_set_hash="c" * 64,
            result_digest="d" * 64,
            result_context={},
            is_rated=False,
        ))
        session.flush()
        session.add_all([
            MatchParticipant(
                id="b" * 31 + "1", match_id=match_id, player_id=p1.player_id,
                seat_index=0, final_score=100, correct_count=5, answer_count=5,
                placement=1, outcome="win",
            ),
            MatchParticipant(
                id="b" * 31 + "2", match_id=match_id, player_id=p2.player_id,
                seat_index=1, final_score=50, correct_count=2, answer_count=5,
                placement=2, outcome="loss",
            ),
        ])

    with TestClient(create_app(database=ratings_database)):
        pass

    service = RatingService(ratings_database)
    assert service.detect_unrated_eligible_matches() == []
    assert service.player_ratings_summary(p1.player_id)["global_rating"]["rating"] == 1016


# =============================================================================
# 4. HTTP API CONTRACTS AND LEADERBOARD TESTS
# =============================================================================

def test_rankings_api_global_leaderboard(ratings_app):
    client, db = ratings_app
    identities = PlayerIdentityService(db)
    history = MatchHistoryService(db)

    # Create 3 players
    p1 = identities.create("Alice")
    p2 = identities.create("Bob")
    p3 = identities.create("Charlie")

    # Match: Alice 1st, Bob 2nd, Charlie 3rd
    match = _make_match_result(
        engine_match_id="5" * 32,
        mode="classic",
        participants=[
            {"seat_index": 0, "player_id": p1.player_id, "placement": 1, "outcome": "win", "final_score": 90, "correct_count": 3, "answer_count": 3, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 1, "player_id": p2.player_id, "placement": 2, "outcome": "loss", "final_score": 60, "correct_count": 2, "answer_count": 3, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 2, "player_id": p3.player_id, "placement": 3, "outcome": "loss", "final_score": 30, "correct_count": 1, "answer_count": 3, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
        ],
    )
    history.persist_completed(match)

    # Public GET /api/rankings
    res = client.get("/api/rankings")
    assert res.status_code == 200
    body = res.json()
    assert body["scope"] == "global"
    assert body["total"] == 3
    assert len(body["rankings"]) == 3

    # Check rank ordering: Alice (1016) > Bob (1000) > Charlie (984)
    r1, r2, r3 = body["rankings"][0], body["rankings"][1], body["rankings"][2]
    assert r1["display_name"] == "Alice"
    assert r1["rank"] == 1
    assert r1["rating"] == 1016
    assert r1["is_provisional"] is True  # 1 match < 10

    assert r2["display_name"] == "Bob"
    assert r2["rank"] == 2
    assert r2["rating"] == 1000

    assert r3["display_name"] == "Charlie"
    assert r3["rank"] == 3
    assert r3["rating"] == 984

    second_page = client.get("/api/rankings?limit=2&offset=2")
    assert second_page.status_code == 200
    assert second_page.json()["total"] == 3
    assert [row["rank"] for row in second_page.json()["rankings"]] == [3]


def test_rankings_api_mode_leaderboard(ratings_app):
    client, db = ratings_app
    identities = PlayerIdentityService(db)
    history = MatchHistoryService(db)

    p1 = identities.create("Diana")
    p2 = identities.create("Edward")

    # Speed challenge match
    match = _make_match_result(
        engine_match_id="6" * 32,
        mode="speed_challenge",
        participants=[
            {"seat_index": 0, "player_id": p1.player_id, "placement": 1, "outcome": "win", "final_score": 100, "correct_count": 5, "answer_count": 5, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 1, "player_id": p2.player_id, "placement": 2, "outcome": "loss", "final_score": 50, "correct_count": 2, "answer_count": 5, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
        ],
    )
    history.persist_completed(match)

    # Query speed_challenge mode leaderboard
    res = client.get("/api/rankings?scope=mode&mode=speed_challenge")
    assert res.status_code == 200
    body = res.json()
    assert body["scope"] == "mode:speed_challenge"
    assert body["mode"] == "speed_challenge"
    assert body["total"] == 2

    # Query classic mode leaderboard (should be 0 for these players)
    res_classic = client.get("/api/rankings?scope=mode&mode=classic")
    assert res_classic.status_code == 200
    assert res_classic.json()["total"] == 0

    # Query invalid scope without mode -> 422
    assert client.get("/api/rankings?scope=mode").status_code == 422
    # Query invalid mode -> 422
    assert client.get("/api/rankings?scope=mode&mode=solo_practice").status_code == 422
    assert client.get("/api/rankings?cursor=not-a-cursor").status_code == 422


def test_player_me_ratings_and_history_endpoints(ratings_app):
    client, db = ratings_app
    identities = PlayerIdentityService(db)
    history = MatchHistoryService(db)

    p1 = identities.create("Fiona")
    p2 = identities.create("George")
    p3 = identities.create("Harper")
    p4 = identities.create("Indigo")

    # Match in Quiz Royale
    match = _make_match_result(
        engine_match_id="7" * 32,
        mode="quiz_royale",
        participants=[
            {"seat_index": 0, "player_id": p1.player_id, "placement": 1, "outcome": "win", "final_score": 80, "correct_count": 4, "answer_count": 4, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 1, "player_id": p2.player_id, "placement": 2, "outcome": "loss", "final_score": 40, "correct_count": 2, "answer_count": 4, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 2, "player_id": p3.player_id, "placement": 3, "outcome": "loss", "final_score": 20, "correct_count": 1, "answer_count": 4, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 3, "player_id": p4.player_id, "placement": 4, "outcome": "loss", "final_score": 10, "correct_count": 0, "answer_count": 4, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
        ],
    )
    history.persist_completed(match)

    auth_header = {"Authorization": f"Bearer {p1.token}"}

    # 1. GET /api/player-profiles/me/ratings
    res = client.get("/api/player-profiles/me/ratings", headers=auth_header)
    assert res.status_code == 200
    body = res.json()
    assert body["player_id"] == p1.player_id
    assert body["display_name"] == "Fiona"
    assert body["global_rating"]["rating"] == 1016
    assert "quiz_royale" in body["mode_ratings"]
    assert len(body["recent_changes"]) == 2

    # 2. GET /api/player-profiles/me/ratings/history
    res_hist = client.get("/api/player-profiles/me/ratings/history", headers=auth_header)
    assert res_hist.status_code == 200
    hist_body = res_hist.json()
    assert hist_body["total"] == 2
    assert len(hist_body["history"]) == 2
    entry = hist_body["history"][0]
    assert entry["prior_rating"] == 1000
    assert entry["rating_delta"] == 16
    assert entry["new_rating"] == 1016
    assert entry["algorithm_version"] == "elo_v1"

    # Filter history by scope
    res_global = client.get("/api/player-profiles/me/ratings/history?scope=global", headers=auth_header)
    assert res_global.status_code == 200
    assert res_global.json()["total"] == 1
    assert res_global.json()["history"][0]["scope"] == "global"
    assert client.get(
        "/api/player-profiles/me/ratings/history?scope=mode:not-a-mode",
        headers=auth_header,
    ).status_code == 422

    history_page = client.get(
        "/api/player-profiles/me/ratings/history?limit=1&offset=1",
        headers=auth_header,
    )
    assert history_page.status_code == 200
    assert history_page.json()["total"] == 2
    assert history_page.json()["offset"] == 1
    assert len(history_page.json()["history"]) == 1


def test_rating_history_has_no_mutation_routes(ratings_app):
    client, _ = ratings_app
    headers = {"Authorization": "Bearer " + "x" * 50}
    path = "/api/player-profiles/me/ratings/history"
    assert client.post(path, headers=headers, json={"rating": 9999}).status_code == 405
    assert client.put(path, headers=headers, json={"rating": 9999}).status_code == 405
    assert client.patch(path, headers=headers, json={"rating": 9999}).status_code == 405


# =============================================================================
# 5. SECURITY AND PRIVACY TESTS
# =============================================================================

def test_unauthenticated_requests_to_private_ratings_are_rejected(ratings_app):
    client, _ = ratings_app
    assert client.get("/api/player-profiles/me/ratings").status_code == 401
    assert client.get("/api/player-profiles/me/ratings/history").status_code == 401

    fake_header = {"Authorization": "Bearer " + "x" * 50}
    assert client.get("/api/player-profiles/me/ratings", headers=fake_header).status_code == 401


def test_public_rankings_do_not_expose_credentials_or_anonymized_profiles(ratings_app):
    client, db = ratings_app
    identities = PlayerIdentityService(db)
    history = MatchHistoryService(db)

    p1 = identities.create("ActivePlayer")
    p2 = identities.create("ToBeDeleted")

    match = _make_match_result(
        engine_match_id="8" * 32,
        mode="power_play",
        participants=[
            {"seat_index": 0, "player_id": p1.player_id, "placement": 1, "outcome": "win", "final_score": 100, "correct_count": 5, "answer_count": 5, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 1, "player_id": p2.player_id, "placement": 2, "outcome": "loss", "final_score": 50, "correct_count": 2, "answer_count": 5, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
        ],
    )
    history.persist_completed(match)

    # Anonymize (delete) p2
    identities.anonymize(p2.player_id)

    # Query rankings: only ActivePlayer should appear!
    res = client.get("/api/rankings")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["rankings"][0]["display_name"] == "ActivePlayer"

    # Verify no credential leaks in response
    serialized = str(data)
    assert "token" not in serialized
    assert "credential" not in serialized
    assert "hash" not in serialized


def test_no_client_rating_modification_endpoint_exists(ratings_app):
    client, _ = ratings_app
    # Verify no PUT/POST/PATCH exists to forge ratings
    assert client.post("/api/rankings", json={"rating": 9999}).status_code == 405
    assert client.put("/api/rankings", json={"rating": 9999}).status_code == 405
    assert client.patch("/api/rankings", json={"rating": 9999}).status_code == 405
    assert client.post("/api/player-profiles/me/ratings", json={"rating": 9999}).status_code == 405

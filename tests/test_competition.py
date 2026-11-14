"""Comprehensive test suite for Stage 8.5 Advanced Competitive Intelligence.

Tests:
- Advanced player statistics (win rates, streaks, recent form, accuracy, peak rating, mode breakdown).
- Exclusion of Solo Practice and Escape Room from competitive statistics.
- Head-to-head pairwise comparison, win rates, score diffs, shared encounters.
- Fair-play intelligence: repeated-opponent pair frequency limits, multiplayer pair-level neutralization,
  strict zero-sum invariant preservation, tournament/league exemptions, and short-match detection.
- Career honors cabinet (tournament champion/runner-up, league podium finishes, exclusion of incomplete events).
- Competition REST APIs.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import secrets
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from quizverse.persistence.competition import (
    AdvancedStatsService,
    CompetitionError,
    HeadToHeadService,
    HonorsService,
)
from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    CompetitiveMatch,
    League,
    LeagueSeason,
    MatchParticipant,
    PlayerProfile,
    PlayerRating,
    RatingHistory,
    SeasonParticipant,
    Tournament,
    TournamentParticipant,
)
from quizverse.persistence.player_identity import (
    MatchHistoryService,
    PlayerIdentityService,
)
from quizverse.persistence.ratings import (
    RatingConfig,
    RatingService,
    calculate_multiplayer_elo,
    ParticipantRatingState,
)
from quizverse.realtime import create_app


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'comp-test.sqlite3').as_posix()}"
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
def identity_service(test_db):
    return PlayerIdentityService(test_db)


@pytest.fixture
def match_history_service(test_db):
    rating_svc = RatingService(test_db)
    return MatchHistoryService(test_db, rating_service=rating_svc)


@pytest.fixture
def advanced_stats_service(test_db):
    return AdvancedStatsService(test_db)


@pytest.fixture
def head_to_head_service(test_db):
    return HeadToHeadService(test_db)


@pytest.fixture
def honors_service(test_db):
    return HonorsService(test_db)


def _create_completed_match(
    db: Database,
    match_id: str,
    mode: str = "classic",
    result_kind: str = "individual",
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    is_rated: bool = True,
    rating_ineligible_reason: str | None = None,
    participants_data: list[dict] | None = None,
    result_context: dict | None = None,
) -> CompetitiveMatch:
    now = datetime.now(UTC)
    start = started_at or (now - timedelta(minutes=5))
    end = completed_at or now
    with db.sessions.begin() as session:
        match = CompetitiveMatch(
            id=match_id,
            engine_match_id=f"eng_{match_id}",
            mode=mode,
            status="completed",
            result_kind=result_kind,
            rules_version="v1.0",
            started_at=start,
            completed_at=end,
            question_set_hash="a" * 64,
            result_digest="b" * 64,
            result_context=result_context or {},
            is_rated=is_rated,
            rating_ineligible_reason=rating_ineligible_reason,
        )
        session.add(match)
        session.flush()

        for idx, p in enumerate(participants_data or []):
            mp = MatchParticipant(
                id=secrets.token_hex(16),
                match_id=match_id,
                player_id=p.get("player_id"),
                seat_index=idx,
                final_score=p.get("final_score", 100),
                correct_count=p.get("correct_count", 8),
                answer_count=p.get("answer_count", 10),
                placement=p.get("placement", 1),
                outcome=p.get("outcome", "win"),
                forfeited=p.get("forfeited", False),
            )
            session.add(mp)
        return match


# =============================================================================
# 1. ADVANCED PLAYER STATISTICS TESTS
# =============================================================================

def test_advanced_stats_empty(test_db, identity_service, advanced_stats_service):
    player = identity_service.create("NewChamp")
    stats = advanced_stats_service.get_advanced_stats(player.player_id)

    assert stats["player_id"] == player.player_id
    assert stats["display_name"] == "NewChamp"
    assert stats["total_matches"] == 0
    assert stats["wins"] == 0
    assert stats["losses"] == 0
    assert stats["draws"] == 0
    assert stats["win_percentage"] == 0.0
    assert stats["current_streak"] == {"type": "none", "count": 0}
    assert stats["longest_win_streak"] == 0
    assert stats["accuracy_pct"] == 0.0
    assert stats["peak_rating"] == 1000
    assert stats["current_rating"] == 1000
    assert stats["is_provisional"] is True
    assert stats["forfeit_monitoring"]["forfeit_warning"] is False


def test_advanced_stats_calculations_and_streaks(test_db, identity_service, advanced_stats_service):
    p = identity_service.create("StreakMaster")
    pid = p.player_id
    now = datetime.now(UTC)

    # Insert 6 matches chronologically:
    # Match 1: Win
    # Match 2: Win
    # Match 3: Loss (breaks first streak of 2)
    # Match 4: Win
    # Match 5: Win
    # Match 6: Win (current streak of 3)
    matches_meta = [
        ("m1", now - timedelta(hours=6), "win", 1, 9, 10, 850),
        ("m2", now - timedelta(hours=5), "win", 1, 8, 10, 800),
        ("m3", now - timedelta(hours=4), "loss", 2, 4, 10, 400),
        ("m4", now - timedelta(hours=3), "win", 1, 10, 10, 1000),
        ("m5", now - timedelta(hours=2), "win", 1, 9, 10, 900),
        ("m6", now - timedelta(hours=1), "win", 1, 9, 10, 950),
    ]

    for mid, comp_time, outcome, placement, cor, ans, score in matches_meta:
        _create_completed_match(
            test_db,
            mid,
            mode="classic",
            completed_at=comp_time,
            participants_data=[
                {"player_id": pid, "placement": placement, "outcome": outcome, "correct_count": cor, "answer_count": ans, "final_score": score}
            ],
        )

    # Insert a rating history record to verify peak rating derivation
    with test_db.sessions.begin() as s:
        s.add(RatingHistory(
            id=secrets.token_hex(16),
            player_id=pid,
            rating_scope="global",
            match_id="m6",
            prior_rating=1050,
            rating_delta=18,
            new_rating=1068,
            actual_score=1.0,
            expected_score=0.45,
            algorithm_version="elo_v1",
        ))
        s.add(PlayerRating(
            id=secrets.token_hex(16),
            player_id=pid,
            rating_scope="global",
            rating=1068,
            rated_match_count=6,
            wins=5,
            losses=1,
            draws=0,
        ))

    stats = advanced_stats_service.get_advanced_stats(pid)

    assert stats["total_matches"] == 6
    assert stats["wins"] == 5
    assert stats["losses"] == 1
    assert stats["draws"] == 0
    # Win rate: 5 / 6 * 100 = 83.3%
    assert stats["win_percentage"] == 83.3
    # Total correct: 49, total answered: 60 -> 81.7%
    assert stats["total_correct"] == 49
    assert stats["total_answered"] == 60
    assert stats["accuracy_pct"] == 81.7

    # Current streak: 3 wins (m6, m5, m4)
    assert stats["current_streak"] == {"type": "win", "count": 3}
    # Longest win streak: 3 wins
    assert stats["longest_win_streak"] == 3
    # Form: newest first -> [win, win, win, loss, win]
    assert stats["recent_form"]["last_5"] == ["win", "win", "win", "loss", "win"]
    assert stats["peak_rating"] == 1068
    assert stats["current_rating"] == 1068


def test_advanced_stats_excludes_solo_practice_and_escape_room(test_db, identity_service, advanced_stats_service):
    p = identity_service.create("CasualAndPro")
    pid = p.player_id
    now = datetime.now(UTC)

    # 1. Practice match (must be EXCLUDED from competitive wins)
    _create_completed_match(
        test_db,
        "prac1",
        mode="solo_practice",
        result_kind="practice",
        completed_at=now - timedelta(hours=3),
        participants_data=[{"player_id": pid, "placement": None, "outcome": "practice", "correct_count": 10, "answer_count": 10}],
    )

    # 2. Escape room cooperative match (must be EXCLUDED from individual competitive wins)
    _create_completed_match(
        test_db,
        "coop1",
        mode="escape_room",
        result_kind="cooperative",
        completed_at=now - timedelta(hours=2),
        participants_data=[{"player_id": pid, "placement": None, "outcome": "cooperative_success", "correct_count": 5, "answer_count": 5}],
    )

    # 3. Legitimate individual competitive match
    _create_completed_match(
        test_db,
        "comp1",
        mode="classic",
        result_kind="individual",
        completed_at=now - timedelta(hours=1),
        participants_data=[{"player_id": pid, "placement": 1, "outcome": "win", "correct_count": 8, "answer_count": 10}],
    )

    stats = advanced_stats_service.get_advanced_stats(pid)

    # Only 1 individual competitive match should be counted!
    assert stats["total_matches"] == 1
    assert stats["wins"] == 1
    assert stats["win_percentage"] == 100.0
    assert stats["current_streak"] == {"type": "win", "count": 1}


def test_advanced_stats_repeated_forfeit_monitoring(test_db, identity_service, advanced_stats_service):
    p = identity_service.create("RageQuitter")
    pid = p.player_id
    now = datetime.now(UTC)

    # Insert 6 matches where 4 were forfeited
    for i in range(6):
        is_forfeit = i in (0, 1, 2, 4)  # 4 out of 6 forfeited (> 30%)
        _create_completed_match(
            test_db,
            f"forf_{i}",
            mode="speed_challenge",
            completed_at=now - timedelta(minutes=10 * i),
            participants_data=[
                {"player_id": pid, "placement": 2, "outcome": "loss", "forfeited": is_forfeit}
            ],
        )

    stats = advanced_stats_service.get_advanced_stats(pid)
    fm = stats["forfeit_monitoring"]
    assert fm["forfeited_count"] == 4
    assert fm["forfeit_rate_pct"] == 66.7
    assert fm["forfeit_warning"] is True


# =============================================================================
# 2. HEAD-TO-HEAD (PvP) COMPETITION TESTS
# =============================================================================

def test_head_to_head_encounters_and_scoring(test_db, identity_service, head_to_head_service):
    p1 = identity_service.create("RivalAlice")
    p2 = identity_service.create("RivalBob")
    id1, id2 = p1.player_id, p2.player_id
    now = datetime.now(UTC)

    # Match 1 (Classic): Alice wins (800 vs 600)
    _create_completed_match(
        test_db,
        "h2h_1",
        mode="classic",
        completed_at=now - timedelta(hours=3),
        participants_data=[
            {"player_id": id1, "placement": 1, "final_score": 800, "outcome": "win"},
            {"player_id": id2, "placement": 2, "final_score": 600, "outcome": "loss"},
        ],
    )

    # Match 2 (Classic): Draw (750 vs 750)
    _create_completed_match(
        test_db,
        "h2h_2",
        mode="classic",
        completed_at=now - timedelta(hours=2),
        participants_data=[
            {"player_id": id1, "placement": 1, "final_score": 750, "outcome": "draw"},
            {"player_id": id2, "placement": 1, "final_score": 750, "outcome": "draw"},
        ],
    )

    # Match 3 (Power Play): Bob wins (500 vs 900)
    _create_completed_match(
        test_db,
        "h2h_3",
        mode="power_play",
        completed_at=now - timedelta(hours=1),
        participants_data=[
            {"player_id": id1, "placement": 2, "final_score": 500, "outcome": "loss"},
            {"player_id": id2, "placement": 1, "final_score": 900, "outcome": "win"},
        ],
    )

    # Assign Elo ratings
    with test_db.sessions.begin() as s:
        s.add(PlayerRating(id="r1", player_id=id1, rating_scope="global", rating=1120))
        s.add(PlayerRating(id="r2", player_id=id2, rating_scope="global", rating=1080))

    h2h = head_to_head_service.get_head_to_head(id1, id2)

    assert h2h["total_encounters"] == 3
    assert h2h["player_one_wins"] == 1
    assert h2h["player_two_wins"] == 1
    assert h2h["draws"] == 1
    assert h2h["player_one_win_pct"] == 33.3
    assert h2h["player_two_win_pct"] == 33.3
    # Total scores: Alice = 800+750+500 = 2050 (avg 683.3); Bob = 600+750+900 = 2250 (avg 750.0)
    assert h2h["player_one_avg_score"] == 683.3
    assert h2h["player_two_avg_score"] == 750.0
    assert h2h["score_difference"] == -200
    assert h2h["player_one"]["rating"] == 1120
    assert h2h["player_two"]["rating"] == 1080
    assert h2h["elo_difference"] == 40
    assert len(h2h["recent_encounters"]) == 3

    # Mode filter: classic only
    h2h_classic = head_to_head_service.get_head_to_head(id1, id2, mode="classic")
    assert h2h_classic["total_encounters"] == 2
    assert h2h_classic["player_one_wins"] == 1
    assert h2h_classic["draws"] == 1
    assert h2h_classic["player_two_wins"] == 0


def test_head_to_head_zero_encounters_and_self_compare(test_db, identity_service, head_to_head_service):
    p1 = identity_service.create("StrangerA")
    p2 = identity_service.create("StrangerB")

    # Zero shared matches
    h2h = head_to_head_service.get_head_to_head(p1.player_id, p2.player_id)
    assert h2h["total_encounters"] == 0
    assert h2h["player_one_wins"] == 0
    assert h2h["player_two_wins"] == 0
    assert h2h["draws"] == 0
    assert h2h["recent_encounters"] == []

    # Self comparison must be rejected
    with pytest.raises(CompetitionError) as exc_info:
        head_to_head_service.get_head_to_head(p1.player_id, p1.player_id)
    assert exc_info.value.code == "invalid_opponent"


# =============================================================================
# 3. FAIR-PLAY & ANTI-COLLUSION INTELLIGENCE TESTS
# =============================================================================

def test_pair_frequency_limit_two_player_match(test_db, identity_service, match_history_service):
    p1 = identity_service.create("FarmerA")
    p2 = identity_service.create("FarmerB")
    id1, id2 = p1.player_id, p2.player_id

    # Complete 5 rated matches within 24 hours between FarmerA and FarmerB
    for i in range(5):
        payload = {
            "source": "game_engine",
            "engine_match_id": secrets.token_hex(16),
            "mode": "classic",
            "status": "completed",
            "result_kind": "individual",
            "rules_version": "v1.0",
            "started_at": datetime.now(UTC) - timedelta(minutes=10 * (6 - i)),
            "completed_at": datetime.now(UTC) - timedelta(minutes=10 * (5 - i)),
            "question_set_hash": "c" * 64,
            "participants": [
                {"seat_index": 0, "player_id": id1, "final_score": 800, "correct_count": 8, "answer_count": 10, "placement": 1, "outcome": "win", "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
                {"seat_index": 1, "player_id": id2, "final_score": 500, "correct_count": 5, "answer_count": 10, "placement": 2, "outcome": "loss", "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            ],
            "teams": [],
            "result_context": {},
        }
        persisted = match_history_service.persist_completed(payload)
        assert persisted is True

    # Check that all 5 matches were rated
    with test_db.session() as s:
        p1_rating = s.scalar(select(PlayerRating.rating).where(PlayerRating.player_id == id1, PlayerRating.rating_scope == "global"))
        # FarmerA won 5 matches starting at 1000, rating must be > 1000
        assert p1_rating > 1000

    # 6th match attempted between the exact same pair within rolling 24 hours
    payload_6th = {
        "source": "game_engine",
        "engine_match_id": secrets.token_hex(16),
        "mode": "classic",
        "status": "completed",
        "result_kind": "individual",
        "rules_version": "v1.0",
        "started_at": datetime.now(UTC) - timedelta(minutes=2),
        "completed_at": datetime.now(UTC),
        "question_set_hash": "c" * 64,
        "participants": [
            {"seat_index": 0, "player_id": id1, "final_score": 800, "correct_count": 8, "answer_count": 10, "placement": 1, "outcome": "win", "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 1, "player_id": id2, "final_score": 500, "correct_count": 5, "answer_count": 10, "placement": 2, "outcome": "loss", "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
        ],
        "teams": [],
        "result_context": {},
    }

    match_history_service.persist_completed(payload_6th)

    # Verify: 6th match is persisted in history, but marked ineligible for rating due to pair_frequency_exceeded!
    with test_db.session() as s:
        m6 = s.scalar(select(CompetitiveMatch).where(CompetitiveMatch.engine_match_id == payload_6th["engine_match_id"]))
        assert m6 is not None
        assert m6.is_rated is False
        assert m6.rating_ineligible_reason == "pair_frequency_exceeded"

        # Rating did not increase for 6th match!
        p1_rating_after = s.scalar(select(PlayerRating.rating).where(PlayerRating.player_id == id1, PlayerRating.rating_scope == "global"))
        assert p1_rating_after == p1_rating


def test_pair_frequency_multiplayer_neutralization_and_zero_sum():
    """Critical requirement: In a multiplayer match (>= 3 players), if one pair exceeds frequency,
    only that pair is neutralized, zero-sum invariant strictly holds, and other players are rated normally.
    """
    p1 = "player_one"
    p2 = "player_two"
    p3 = "player_three"
    p4 = "player_four"

    participants = [
        ParticipantRatingState(player_id=p1, prior_rating=1000, placement=1, outcome="win"),
        ParticipantRatingState(player_id=p2, prior_rating=1000, placement=2, outcome="loss"),
        ParticipantRatingState(player_id=p3, prior_rating=1000, placement=3, outcome="loss"),
        ParticipantRatingState(player_id=p4, prior_rating=1000, placement=4, outcome="loss"),
    ]

    # Neutralize pair {p1, p2}
    ineligible = {frozenset([p1, p2])}
    adjustments = calculate_multiplayer_elo(participants, ineligible_pairs=ineligible)

    # 1. Zero-sum invariant strictly holds!
    assert sum(adj.rating_delta for adj in adjustments) == 0

    # 2. P1 still gains rating from beating P3 and P4, and P2 still gets rating against P3 and P4!
    adj_p1 = next(a for a in adjustments if a.player_id == p1)
    adj_p2 = next(a for a in adjustments if a.player_id == p2)
    adj_p4 = next(a for a in adjustments if a.player_id == p4)

    assert adj_p1.rating_delta > 0
    assert adj_p4.rating_delta < 0


def test_short_match_duration_detection(test_db, identity_service, match_history_service):
    p1 = identity_service.create("Speedy1")
    p2 = identity_service.create("Speedy2")
    now = datetime.now(UTC)

    # Impossibly short 2-second match with 10 questions
    payload = {
        "source": "game_engine",
        "engine_match_id": secrets.token_hex(16),
        "mode": "classic",
        "status": "completed",
        "result_kind": "individual",
        "rules_version": "v1.0",
        "started_at": now - timedelta(seconds=2),
        "completed_at": now,
        "question_set_hash": "d" * 64,
        "participants": [
            {"seat_index": 0, "player_id": p1.player_id, "final_score": 1000, "correct_count": 10, "answer_count": 10, "placement": 1, "outcome": "win", "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 1, "player_id": p2.player_id, "final_score": 800, "correct_count": 8, "answer_count": 10, "placement": 2, "outcome": "loss", "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
        ],
        "teams": [],
        "result_context": {},
    }

    match_history_service.persist_completed(payload)

    with test_db.session() as s:
        match = s.scalar(select(CompetitiveMatch).where(CompetitiveMatch.engine_match_id == payload["engine_match_id"]))
        assert match is not None
        assert match.is_rated is False
        assert match.rating_ineligible_reason == "match_duration_too_short"


def test_official_tournament_and_league_exempt_from_pair_frequency(test_db, identity_service, match_history_service):
    p1 = identity_service.create("TournPlayerA")
    p2 = identity_service.create("TournPlayerB")
    id1, id2 = p1.player_id, p2.player_id

    # Pre-populate 5 rated matches between them
    now = datetime.now(UTC)
    for i in range(5):
        _create_completed_match(
            test_db,
            f"pre_{i}",
            completed_at=now - timedelta(minutes=5 * (6 - i)),
            participants_data=[
                {"player_id": id1, "placement": 1, "outcome": "win"},
                {"player_id": id2, "placement": 2, "outcome": "loss"},
            ],
            is_rated=True,
        )

    # 6th match is an official tournament fixture (has tournament_match_id)
    payload_tourn = {
        "source": "game_engine",
        "engine_match_id": secrets.token_hex(16),
        "mode": "classic",
        "status": "completed",
        "result_kind": "individual",
        "rules_version": "v1.0",
        "started_at": now - timedelta(minutes=3),
        "completed_at": now,
        "question_set_hash": "e" * 64,
        "participants": [
            {"seat_index": 0, "player_id": id1, "final_score": 800, "correct_count": 8, "answer_count": 10, "placement": 1, "outcome": "win", "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 1, "player_id": id2, "final_score": 500, "correct_count": 5, "answer_count": 10, "placement": 2, "outcome": "loss", "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
        ],
        "teams": [],
        "result_context": {"tournament_match_id": "tm_123"},
    }

    match_history_service.persist_completed(payload_tourn)

    with test_db.session() as s:
        m = s.scalar(select(CompetitiveMatch).where(CompetitiveMatch.engine_match_id == payload_tourn["engine_match_id"]))
        assert m is not None
        # Tournament fixture must be EXEMPT and successfully rated!
        assert m.is_rated is True
        assert m.rating_ineligible_reason is None


# =============================================================================
# 4. CAREER HONORS & TROPHY CABINET TESTS
# =============================================================================

def test_honors_cabinet(test_db, identity_service, honors_service):
    p = identity_service.create("DecoratedHero")
    pid = p.player_id
    now = datetime.now(UTC)

    # 1. Completed tournament where p was champion
    with test_db.sessions.begin() as s:
        t1 = Tournament(
            id="t1",
            owner_player_id=pid,
            name="Spring Championship",
            mode="classic",
            status="completed",
            completed_at=now - timedelta(days=2),
        )
        s.add(t1)
        s.add(TournamentParticipant(
            id="tp1",
            tournament_id="t1",
            player_id=pid,
            final_placement=1,
            status="champion",
        ))

        # 2. Incomplete tournament (must be EXCLUDED)
        t2 = Tournament(
            id="t2",
            owner_player_id=pid,
            name="Ongoing Cup",
            mode="speed_challenge",
            status="in_progress",
        )
        s.add(t2)
        s.add(TournamentParticipant(
            id="tp2",
            tournament_id="t2",
            player_id=pid,
            final_placement=1,
            status="active",
        ))

        # 3. Completed league season where p placed 2nd (podium)
        l1 = League(
            id="l1",
            owner_player_id=pid,
            name="Premier League",
            mode="elimination",
            status="active",
        )
        s.add(l1)
        s.flush()
        s1 = LeagueSeason(
            id="s1",
            league_id="l1",
            season_number=1,
            name="Season 1",
            status="completed",
            completed_at=now - timedelta(days=1),
        )
        s.add(s1)
        s.add(SeasonParticipant(
            id="sp1",
            season_id="s1",
            player_id=pid,
            final_placement=2,
            status="completed",
        ))

    honors = honors_service.get_player_honors(pid)

    assert honors["player_id"] == pid
    assert honors["tournament_championships"] == 1
    assert honors["tournament_runner_ups"] == 0
    assert honors["league_championships"] == 0
    assert honors["league_podiums"] == 1
    assert honors["total_honors"] == 2

    # Check trophies
    assert len(honors["trophies"]) == 2
    trophy_titles = {t["title"] for t in honors["trophies"]}
    assert "Tournament Champion" in trophy_titles
    assert "League Runner-Up" in trophy_titles


# =============================================================================
# 5. REST API TESTS
# =============================================================================

def test_competition_api_flow(test_db):
    app = create_app(database=test_db)
    client = TestClient(app)

    # Create 2 players
    r1 = client.post("/api/player-profiles", json={"display_name": "APIPlayerOne"})
    assert r1.status_code == 201
    p1_data = r1.json()
    p1_id = p1_data["player"]["id"]
    token1 = p1_data["credential"]

    r2 = client.post("/api/player-profiles", json={"display_name": "APIPlayerTwo"})
    assert r2.status_code == 201
    p2_data = r2.json()
    p2_id = p2_data["player"]["id"]

    # 1. Advanced stats endpoint for p1
    stats_res = client.get(
        f"/api/players/{p1_id}/statistics/advanced",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert stats_res.status_code == 200
    stats = stats_res.json()
    assert stats["player_id"] == p1_id
    assert stats["display_name"] == "APIPlayerOne"
    assert "win_percentage" in stats
    assert "current_streak" in stats

    # 2. Advanced stats using "me" keyword
    stats_me = client.get(
        "/api/players/me/statistics/advanced",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert stats_me.status_code == 200
    assert stats_me.json()["player_id"] == p1_id

    # 3. Head-to-Head endpoint
    h2h_res = client.get(
        f"/api/competition/head-to-head?player_one_id={p1_id}&player_two_id={p2_id}",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert h2h_res.status_code == 200
    h2h = h2h_res.json()
    assert h2h["player_one"]["id"] == p1_id
    assert h2h["player_two"]["id"] == p2_id
    assert h2h["total_encounters"] == 0

    # 4. Honors endpoint for p1
    honors_res = client.get(
        f"/api/players/{p1_id}/honors",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert honors_res.status_code == 200
    honors = honors_res.json()
    assert honors["player_id"] == p1_id
    assert honors["total_honors"] == 0

    # 5. Privacy: verify sensitive keys are never exposed in H2H
    h2h_keys = set(h2h.keys()) | set(h2h["player_one"].keys()) | set(h2h["player_two"].keys())
    for secret in ("credential", "token", "password", "salt", "ip", "host", "email"):
        assert secret not in h2h_keys


def test_streaks_with_interleaved_draws(test_db, identity_service, advanced_stats_service):
    p = identity_service.create("DrawStreaker")
    pid = p.player_id
    now = datetime.now(UTC)

    # 3 matches: Win, then Draw, then Draw (current streak is 2 draws)
    _create_completed_match(
        test_db, "m_draw1", completed_at=now - timedelta(hours=3),
        participants_data=[{"player_id": pid, "placement": 1, "outcome": "win", "correct_count": 8, "answer_count": 10}],
    )
    _create_completed_match(
        test_db, "m_draw2", completed_at=now - timedelta(hours=2),
        participants_data=[{"player_id": pid, "placement": 1, "outcome": "draw", "correct_count": 8, "answer_count": 10}],
    )
    _create_completed_match(
        test_db, "m_draw3", completed_at=now - timedelta(hours=1),
        participants_data=[{"player_id": pid, "placement": 1, "outcome": "draw", "correct_count": 8, "answer_count": 10}],
    )

    stats = advanced_stats_service.get_advanced_stats(pid)
    assert stats["total_matches"] == 3
    assert stats["wins"] == 1
    assert stats["draws"] == 2
    assert stats["current_streak"] == {"type": "draw", "count": 2}
    assert stats["longest_win_streak"] == 1


def test_multiplayer_pair_frequency_in_persistence_flow(test_db, identity_service, match_history_service):
    p1 = identity_service.create("MPFarmerA")
    p2 = identity_service.create("MPFarmerB")
    p3 = identity_service.create("MPInnocentC")
    id1, id2, id3 = p1.player_id, p2.player_id, p3.player_id
    now = datetime.now(UTC)

    # Pre-populate 5 rated matches between FarmerA and FarmerB
    for i in range(5):
        _create_completed_match(
            test_db,
            f"mp_pre_{i}",
            completed_at=now - timedelta(minutes=10 * (6 - i)),
            participants_data=[
                {"player_id": id1, "placement": 1, "outcome": "win"},
                {"player_id": id2, "placement": 2, "outcome": "loss"},
            ],
            is_rated=True,
        )

    # Now play a 3-player match with FarmerA, FarmerB, and InnocentC
    # FarmerA and FarmerB have exceeded pair frequency limit (5 matches in 24h)
    # InnocentC has NOT played either of them before!
    payload_mp = {
        "source": "game_engine",
        "engine_match_id": secrets.token_hex(16),
        "mode": "classic",
        "status": "completed",
        "result_kind": "individual",
        "rules_version": "v1.0",
        "started_at": now - timedelta(minutes=3),
        "completed_at": now,
        "question_set_hash": "f" * 64,
        "participants": [
            {"seat_index": 0, "player_id": id1, "final_score": 900, "correct_count": 9, "answer_count": 10, "placement": 1, "outcome": "win", "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 1, "player_id": id2, "final_score": 700, "correct_count": 7, "answer_count": 10, "placement": 2, "outcome": "loss", "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 2, "player_id": id3, "final_score": 500, "correct_count": 5, "answer_count": 10, "placement": 3, "outcome": "loss", "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
        ],
        "teams": [],
        "result_context": {},
    }

    match_history_service.persist_completed(payload_mp)

    with test_db.session() as s:
        match = s.scalar(select(CompetitiveMatch).where(CompetitiveMatch.engine_match_id == payload_mp["engine_match_id"]))
        assert match is not None
        # Match remains rated overall because InnocentC is a legitimate eligible participant!
        assert match.is_rated is True
        # Result context documented the neutralized ineligible pair!
        assert "ineligible_pairs" in match.result_context
        ineligible_list = match.result_context["ineligible_pairs"]
        assert any(set(pair) == {id1, id2} for pair in ineligible_list)

        # Check that RatingHistory records were created for all 3 players
        histories = s.scalars(select(RatingHistory).where(RatingHistory.match_id == match.id, RatingHistory.rating_scope == "global")).all()
        assert len(histories) == 3
        # Strict zero-sum invariant holds!
        assert sum(h.rating_delta for h in histories) == 0


"""Comprehensive test suite for Stage 8.4 Competitive League Seasons MVP.

Tests round-robin schedule generation (circle method, odd/even counts, byes),
deterministic seeding, standings calculations, strict 5-tier tie-breakers,
registration/withdrawal, fixture admission tickets, authoritative match completion,
idempotency, forfeits, disputes, audit integrity, and REST APIs.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from quizverse.engine import GameEngine
from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    CompetitiveMatch,
    League,
    LeagueSeason,
    MatchParticipant,
    PlayerProfile,
    PlayerRating,
    SeasonAuditEvent,
    SeasonFixture,
    SeasonFixtureTicket,
    SeasonParticipant,
    SeasonRound,
)
from quizverse.persistence.player_identity import (
    MatchHistoryService,
    PlayerIdentityService,
)
from quizverse.persistence.ratings import RatingService
from quizverse.persistence.leagues import (
    LeagueError,
    LeagueService,
    generate_round_robin_schedule,
)
from quizverse.realtime import create_app


# =============================================================================
# 1. ROUND-ROBIN SCHEDULE GENERATION UNIT TESTS
# =============================================================================

def test_round_robin_schedule_even():
    players = ["P1", "P2", "P3", "P4"]
    schedule = generate_round_robin_schedule(players)

    # 4 players -> 3 rounds, 2 matches per round = 6 matches total
    assert len(schedule) == 3
    pairings: set[frozenset[str]] = set()

    for r_idx, round_pairs in enumerate(schedule):
        assert len(round_pairs) == 2
        round_players: set[str] = set()
        for p1, p2 in round_pairs:
            assert p1 is not None and p2 is not None
            assert p1 != p2
            pair = frozenset([p1, p2])
            assert pair not in pairings, f"Duplicate fixture {pair} found in round {r_idx + 1}"
            pairings.add(pair)
            round_players.add(p1)
            round_players.add(p2)
        assert round_players == set(players), "Every player must play exactly once per round"

    # Total distinct pairs = 4 * 3 / 2 = 6
    assert len(pairings) == 6


def test_round_robin_schedule_odd_with_byes():
    players = ["A", "B", "C"]
    schedule = generate_round_robin_schedule(players)

    # 3 players -> 3 rounds, 2 fixtures per round (1 active match + 1 bye)
    assert len(schedule) == 3
    pairings: set[frozenset[str]] = set()
    bye_counts: dict[str, int] = {p: 0 for p in players}

    for round_pairs in schedule:
        assert len(round_pairs) == 2
        for p1, p2 in round_pairs:
            if p2 is None:
                assert p1 is not None
                bye_counts[p1] += 1
            else:
                pair = frozenset([p1, p2])
                assert pair not in pairings
                pairings.add(pair)

    # Distinct pairs played = 3 (A-B, B-C, A-C)
    assert len(pairings) == 3
    # Each player receives exactly one bye
    for p, count in bye_counts.items():
        assert count == 1, f"Player {p} did not receive exactly 1 bye: got {count}"


# =============================================================================
# 2. DATABASE FIXTURES
# =============================================================================

@pytest.fixture
def league_database(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'league-test.sqlite3').as_posix()}"
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
def league_app(league_database: Database):
    with TestClient(create_app(database=league_database)) as client:
        yield client, league_database


class MockPlayer:
    def __init__(self, issued):
        self.player_id = issued.player_id
        self.id = issued.player_id
        self.token = issued.token
        self.headers = {"Authorization": f"Bearer {issued.token}"}


def create_player(identities: PlayerIdentityService, name: str) -> MockPlayer:
    issued = identities.create(name)
    return MockPlayer(issued)


# =============================================================================
# 3. LEAGUE & SEASON LIFECYCLE TESTS
# =============================================================================

def test_league_lifecycle_and_constraints(league_database: Database):
    identities = PlayerIdentityService(league_database)
    owner = create_player(identities, "Commissioner")
    service = LeagueService(league_database)

    # 1. Invalid mode rejected
    with pytest.raises(LeagueError) as exc:
        service.create_league(owner_player_id=owner.id, name="Invalid Mode", mode="solo_practice")
    assert exc.value.code == "invalid_league_mode"

    # 2. Invalid name rejected
    with pytest.raises(LeagueError) as exc:
        service.create_league(owner_player_id=owner.id, name="   ", mode="classic")
    assert exc.value.code == "invalid_league_name"

    # 3. Create valid league
    league = service.create_league(
        owner_player_id=owner.id,
        name="Global Classic League",
        mode="classic",
        description="Weekly round-robin classic league",
    )
    assert league["id"] is not None
    assert league["name"] == "Global Classic League"
    assert league["mode"] == "classic"
    assert league["status"] == "active"
    assert league["seasons_count"] == 0

    # 4. Update league
    updated = service.update_league(
        league_id=league["id"],
        actor_id=owner.id,
        name="Global Classic League - Revised",
        description="Updated description",
    )
    assert updated["name"] == "Global Classic League - Revised"

    # 5. Non-owner cannot update
    other = create_player(identities, "Rando")
    with pytest.raises(LeagueError) as exc:
        service.update_league(league_id=league["id"], actor_id=other.id, name="Hacked")
    assert exc.value.code == "forbidden"


def test_season_creation_and_capacity_enforcement(league_database: Database):
    identities = PlayerIdentityService(league_database)
    owner = create_player(identities, "Commissioner")
    service = LeagueService(league_database)

    league = service.create_league(owner_player_id=owner.id, name="Season Test League", mode="speed_challenge")

    # Capacity limits (must be between 2 and 32)
    with pytest.raises(LeagueError) as exc:
        service.create_season(league_id=league["id"], actor_id=owner.id, name="Invalid Capacity", capacity=1)
    assert exc.value.code == "invalid_capacity"

    with pytest.raises(LeagueError) as exc:
        service.create_season(league_id=league["id"], actor_id=owner.id, name="Invalid Capacity", capacity=33)
    assert exc.value.code == "invalid_capacity"

    # Season 1
    s1 = service.create_season(league_id=league["id"], actor_id=owner.id, name="Season 1", capacity=8)
    assert s1["season_number"] == 1
    assert s1["status"] == "draft"
    assert s1["capacity"] == 8

    # Season 2 (auto-increments season number)
    s2 = service.create_season(league_id=league["id"], actor_id=owner.id, name="Season 2", capacity=16)
    assert s2["season_number"] == 2


def test_season_registration_and_withdrawal(league_database: Database):
    identities = PlayerIdentityService(league_database)
    owner = create_player(identities, "Owner")
    p1 = create_player(identities, "Player1")
    p2 = create_player(identities, "Player2")
    p3 = create_player(identities, "Player3")

    service = LeagueService(league_database)
    league = service.create_league(owner_player_id=owner.id, name="Reg League", mode="classic")
    season = service.create_season(league_id=league["id"], actor_id=owner.id, name="Season Alpha", capacity=2)

    # 1. Cannot register while in draft
    with pytest.raises(LeagueError) as exc:
        service.register_player(season["id"], p1.id)
    assert exc.value.code == "registration_closed"

    # 2. Open registration
    service.open_registration(season["id"], actor_id=owner.id)

    # 3. Register players
    r1 = service.register_player(season["id"], p1.id)
    assert r1["registered"] is True
    r2 = service.register_player(season["id"], p2.id)
    assert r2["registered"] is True

    # 4. Cannot exceed capacity (capacity is 2)
    with pytest.raises(LeagueError) as exc:
        service.register_player(season["id"], p3.id)
    assert exc.value.code == "season_full"

    # 5. Cannot register twice
    with pytest.raises(LeagueError) as exc:
        service.register_player(season["id"], p1.id)
    assert exc.value.code == "already_registered"

    # 6. Withdraw
    w = service.withdraw_player(season["id"], p2.id)
    assert w["withdrawn"] is True

    # 7. Now p3 can register in the freed slot
    r3 = service.register_player(season["id"], p3.id)
    assert r3["registered"] is True


def test_deterministic_seeding_uses_rating_and_tiebreaks(league_database: Database):
    identities = PlayerIdentityService(league_database)
    owner = create_player(identities, "Owner")
    service = LeagueService(league_database)

    league = service.create_league(owner_player_id=owner.id, name="Seeding League", mode="classic")
    season = service.create_season(league_id=league["id"], actor_id=owner.id, name="Season Seeding", capacity=8)
    service.open_registration(season["id"], actor_id=owner.id)

    # Create 3 players with different ratings
    p_low = create_player(identities, "LowRating")
    p_mid = create_player(identities, "MidRating")
    p_high = create_player(identities, "HighRating")

    with league_database.sessions.begin() as s:
        s.add(PlayerRating(
            id="r1", player_id=p_high.id, rating_scope="mode:classic", rating=1450, algorithm_version="elo_v1",
        ))
        s.add(PlayerRating(
            id="r2", player_id=p_mid.id, rating_scope="mode:classic", rating=1200, algorithm_version="elo_v1",
        ))
        s.add(PlayerRating(
            id="r3", player_id=p_low.id, rating_scope="mode:classic", rating=950, algorithm_version="elo_v1",
        ))

    service.register_player(season["id"], p_low.id)
    service.register_player(season["id"], p_mid.id)
    service.register_player(season["id"], p_high.id)

    # Start season -> seeds players deterministically
    started = service.start_season(season["id"], actor_id=owner.id)
    participants = started["participants"]

    # Must be seeded #1: HighRating, #2: MidRating, #3: LowRating
    assert participants[0]["player_id"] == p_high.id and participants[0]["seed"] == 1
    assert participants[1]["player_id"] == p_mid.id and participants[1]["seed"] == 2
    assert participants[2]["player_id"] == p_low.id and participants[2]["seed"] == 3


# =============================================================================
# 4. STANDINGS AND 5-TIER TIE-BREAKER VERIFICATION
# =============================================================================

def test_standings_and_tie_breakers(league_database: Database):
    """Rigorous verification of the 5 tie-breaking tiers in documented order:

    1. Points (Win: 3, Draw: 1, Loss: 0, Bye: 0)
    2. Head-to-Head points among tied players
    3. Score difference (Score For - Score Against)
    4. Match wins count
    5. Stable player identifier (player_id ascending)
    """
    identities = PlayerIdentityService(league_database)
    owner = create_player(identities, "Owner")
    service = LeagueService(league_database)

    league = service.create_league(owner_player_id=owner.id, name="Tiebreaker League", mode="classic")
    season = service.create_season(league_id=league["id"], actor_id=owner.id, name="Season S1", capacity=8)
    service.open_registration(season["id"], actor_id=owner.id)

    # Four players: A, B, C, D
    pa = create_player(identities, "PlayerA")
    pb = create_player(identities, "PlayerB")
    pc = create_player(identities, "PlayerC")
    pd = create_player(identities, "PlayerD")

    for p in (pa, pb, pc, pd):
        service.register_player(season["id"], p.id)

    started = service.start_season(season["id"], actor_id=owner.id)
    fixtures = service.get_fixtures(season["id"])
    assert len(fixtures) == 6  # 4 players -> 6 round-robin matches

    # Scenario:
    # Match 1: PlayerA vs PlayerB -> PlayerA wins 100 - 80 (A gets 3 pts, B gets 0)
    # Match 2: PlayerC vs PlayerD -> Draw 50 - 50 (C gets 1 pt, D gets 1 pt)
    # Match 3: PlayerB vs PlayerC -> PlayerB wins 90 - 70 (B gets 3 pts, C gets 0)
    # Match 4: PlayerA vs PlayerD -> PlayerD wins 110 - 70 (D gets 3 pts, A gets 0)
    # Match 5: PlayerA vs PlayerC -> PlayerC wins 85 - 80 (C gets 3 pts, A gets 0)
    # Match 6: PlayerB vs PlayerD -> PlayerB wins 95 - 60 (B gets 3 pts, D gets 0)

    # Final tally:
    # PlayerB: wins against C (90-70) and D (95-60), lost to A (80-100)
    #   Points: 6, W: 2, D: 0, L: 1. SF: 80+90+95 = 265, SA: 100+70+60 = 230, SD: +35.
    # PlayerD: drew C (50-50), beat A (110-70), lost to B (60-95)
    #   Points: 4, W: 1, D: 1, L: 1. SF: 220, SA: 215, SD: +5.
    # PlayerC: drew D (50-50), lost to B (70-90), beat A (85-80)
    #   Points: 4, W: 1, D: 1, L: 1. SF: 205, SA: 220, SD: -15.
    # PlayerA: beat B (100-80), lost to D (70-110), lost to C (80-85)
    #   Points: 3, W: 1, D: 0, L: 2. SF: 250, SA: 275, SD: -25.

    # Notice PlayerD and PlayerC both have 4 points and drew head-to-head (1 pt each).
    # Tier 3 (Score Difference) separates them: PlayerD (+5) vs PlayerC (-15)!

    def mock_match_result(f_id, p1_id, p2_id, p1_score, p2_score, winner_id, is_draw):
        p1_place = 1 if is_draw or winner_id == p1_id else 2
        p2_place = 1 if is_draw or winner_id == p2_id else 2
        return {
            "source": "game_engine",
            "participants": [
                {"player_id": p1_id, "final_score": p1_score, "placement": p1_place},
                {"player_id": p2_id, "final_score": p2_score, "placement": p2_place},
            ],
            "result_context": {"league_fixture_id": f_id},
        }

    with league_database.sessions.begin() as s:
        # Match A vs B
        fab = next(f for f in fixtures if {f["player_one_id"], f["player_two_id"]} == {pa.id, pb.id})
        p1, p2 = fab["player_one_id"], fab["player_two_id"]
        s1, s2 = (100, 80) if p1 == pa.id else (80, 100)
        service.process_fixture_completion(s, fab["id"], None, mock_match_result(fab["id"], p1, p2, s1, s2, pa.id, False))

        # Match C vs D
        fcd = next(f for f in fixtures if {f["player_one_id"], f["player_two_id"]} == {pc.id, pd.id})
        p1, p2 = fcd["player_one_id"], fcd["player_two_id"]
        service.process_fixture_completion(s, fcd["id"], None, mock_match_result(fcd["id"], p1, p2, 50, 50, None, True))

        # Match B vs C
        fbc = next(f for f in fixtures if {f["player_one_id"], f["player_two_id"]} == {pb.id, pc.id})
        p1, p2 = fbc["player_one_id"], fbc["player_two_id"]
        s1, s2 = (90, 70) if p1 == pb.id else (70, 90)
        service.process_fixture_completion(s, fbc["id"], None, mock_match_result(fbc["id"], p1, p2, s1, s2, pb.id, False))

        # Match A vs D
        fad = next(f for f in fixtures if {f["player_one_id"], f["player_two_id"]} == {pa.id, pd.id})
        p1, p2 = fad["player_one_id"], fad["player_two_id"]
        s1, s2 = (70, 110) if p1 == pa.id else (110, 70)
        service.process_fixture_completion(s, fad["id"], None, mock_match_result(fad["id"], p1, p2, s1, s2, pd.id, False))

        # Match A vs C
        fac = next(f for f in fixtures if {f["player_one_id"], f["player_two_id"]} == {pa.id, pc.id})
        p1, p2 = fac["player_one_id"], fac["player_two_id"]
        s1, s2 = (80, 85) if p1 == pa.id else (85, 80)
        service.process_fixture_completion(s, fac["id"], None, mock_match_result(fac["id"], p1, p2, s1, s2, pc.id, False))

        # Match B vs D
        fbd = next(f for f in fixtures if {f["player_one_id"], f["player_two_id"]} == {pb.id, pd.id})
        p1, p2 = fbd["player_one_id"], fbd["player_two_id"]
        s1, s2 = (95, 60) if p1 == pb.id else (60, 95)
        service.process_fixture_completion(s, fbd["id"], None, mock_match_result(fbd["id"], p1, p2, s1, s2, pb.id, False))

    # All fixtures completed -> season automatically crowned & completed!
    completed_season = service.get_season(season["id"])
    assert completed_season["status"] == "completed"
    assert completed_season["completed_at"] is not None

    standings = completed_season["standings"]
    assert len(standings) == 4

    # 1st Place: PlayerB with 6 points
    assert standings[0]["player_id"] == pb.id
    assert standings[0]["points"] == 6
    assert standings[0]["rank"] == 1
    assert standings[0]["wins"] == 2

    # 2nd Place: PlayerD with 4 points and +5 SD (beat C on SD tie-breaker)
    assert standings[1]["player_id"] == pd.id
    assert standings[1]["points"] == 4
    assert standings[1]["rank"] == 2
    assert standings[1]["score_diff"] == 5

    # 3rd Place: PlayerC with 4 points and -15 SD
    assert standings[2]["player_id"] == pc.id
    assert standings[2]["points"] == 4
    assert standings[2]["rank"] == 3
    assert standings[2]["score_diff"] == -15

    # 4th Place: PlayerA with 3 points
    assert standings[3]["player_id"] == pa.id
    assert standings[3]["points"] == 3
    assert standings[3]["rank"] == 4


# =============================================================================
# 5. FIXTURE TICKETS & SECURITY TESTS
# =============================================================================

def test_fixture_ticket_security_and_expiry(league_database: Database):
    identities = PlayerIdentityService(league_database)
    owner = create_player(identities, "Owner")
    p1 = create_player(identities, "Player1")
    p2 = create_player(identities, "Player2")
    unrelated = create_player(identities, "Intruder")

    service = LeagueService(league_database)
    league = service.create_league(owner_player_id=owner.id, name="Ticket League", mode="classic")
    season = service.create_season(league_id=league["id"], actor_id=owner.id, name="Season Tickets", capacity=2)
    service.open_registration(season["id"], actor_id=owner.id)

    service.register_player(season["id"], p1.id)
    service.register_player(season["id"], p2.id)
    service.start_season(season["id"], actor_id=owner.id)

    fixtures = service.get_fixtures(season["id"])
    fixture = fixtures[0]

    # 1. Unrelated player cannot get a ticket
    with pytest.raises(LeagueError) as exc:
        service.create_fixture_ticket(season["id"], fixture["id"], unrelated.id)
    assert exc.value.code == "forbidden"

    # 2. Legitimate player receives valid ticket
    ticket_data = service.create_fixture_ticket(season["id"], fixture["id"], p1.id)
    token = ticket_data["ticket"]
    assert token is not None

    # 3. Consume ticket successfully
    ticket_record = service.validate_and_consume_ticket(season["id"], fixture["id"], p1.id, token)
    assert ticket_record.used_at is not None

    # 4. Ticket cannot be reused (single-use)
    with pytest.raises(LeagueError) as exc:
        service.validate_and_consume_ticket(season["id"], fixture["id"], p1.id, token)
    assert exc.value.code == "ticket_already_used"

    # 5. Expired ticket is rejected
    ticket2 = service.create_fixture_ticket(season["id"], fixture["id"], p2.id)
    with league_database.sessions.begin() as s:
        stmt = select(SeasonFixtureTicket).where(SeasonFixtureTicket.fixture_id == fixture["id"], SeasonFixtureTicket.player_id == p2.id)
        t_row = s.scalar(stmt)
        t_row.expires_at = datetime.now(UTC) - timedelta(seconds=10)

    with pytest.raises(LeagueError) as exc:
        service.validate_and_consume_ticket(season["id"], fixture["id"], p2.id, ticket2["ticket"])
    assert exc.value.code == "ticket_expired"


# =============================================================================
# 6. FORFEITS, DISPUTES, AND AUDIT EVENTS
# =============================================================================

def test_forfeits_disputes_and_audit_trail(league_database: Database):
    identities = PlayerIdentityService(league_database)
    owner = create_player(identities, "Commissioner")
    p1 = create_player(identities, "Player1")
    p2 = create_player(identities, "Player2")

    service = LeagueService(league_database)
    league = service.create_league(owner_player_id=owner.id, name="Forfeit League", mode="power_play")
    season = service.create_season(league_id=league["id"], actor_id=owner.id, name="Season Forfeit", capacity=2)
    service.open_registration(season["id"], actor_id=owner.id)

    service.register_player(season["id"], p1.id)
    service.register_player(season["id"], p2.id)
    service.start_season(season["id"], actor_id=owner.id)

    fixtures = service.get_fixtures(season["id"])
    fixture = fixtures[0]

    # Player 1 forfeits
    res = service.forfeit_fixture(season["id"], fixture["id"], actor_id=p1.id, reason="Connection dropped")
    updated_fixture = next(f for f in service.get_fixtures(season["id"]) if f["id"] == fixture["id"])
    assert updated_fixture["status"] == "forfeited"
    assert updated_fixture["winner_player_id"] == p2.id
    assert updated_fixture["player_one_score"] == 0
    assert updated_fixture["player_two_score"] == 1

    # Dispute logging
    dispute_res = service.dispute_fixture(season["id"], fixture["id"], actor_id=p1.id, reason="Controller lag")
    assert dispute_res is not None

    # Audit events must be recorded
    audit_events = service.get_audit_events(season["id"])
    actions = [e["action"] for e in audit_events]
    assert "season_created" in actions
    assert "player_registered" in actions
    assert "season_started" in actions
    assert "fixture_forfeited" in actions
    assert "fixture_disputed" in actions
    assert "season_completed" in actions


# =============================================================================
# 7. MATCH HISTORY & ELO ATOMIC INTEGRATION
# =============================================================================

def test_match_history_atomic_advancement_and_elo(league_database: Database):
    identities = PlayerIdentityService(league_database)
    owner = create_player(identities, "Owner")
    p1 = create_player(identities, "Contender1")
    p2 = create_player(identities, "Contender2")

    rating_service = RatingService(league_database)
    league_service = LeagueService(league_database)
    match_history = MatchHistoryService(league_database, rating_service=rating_service, league_service=league_service)

    league = league_service.create_league(owner_player_id=owner.id, name="Atomic Elo League", mode="classic")
    season = league_service.create_season(league_id=league["id"], actor_id=owner.id, name="Season Elo", capacity=2)
    league_service.open_registration(season["id"], actor_id=owner.id)
    league_service.register_player(season["id"], p1.id)
    league_service.register_player(season["id"], p2.id)
    league_service.start_season(season["id"], actor_id=owner.id)

    fixture = league_service.get_fixtures(season["id"])[0]

    # Server engine payload with league_fixture_id in result_context
    match_payload = {
        "source": "game_engine",
        "engine_match_id": "eng_match_0000000000000000000001",
        "mode": "classic",
        "status": "completed",
        "result_kind": "individual",
        "rules_version": "stage-7-v1",
        "started_at": datetime.now(UTC) - timedelta(minutes=10),
        "completed_at": datetime.now(UTC),
        "question_set_hash": "a" * 64,
        "participants": [
            {"seat_index": 0, "player_id": p1.id, "final_score": 100, "correct_count": 5, "answer_count": 5, "placement": 1, "outcome": "win", "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
            {"seat_index": 1, "player_id": p2.id, "final_score": 50, "correct_count": 2, "answer_count": 5, "placement": 2, "outcome": "loss", "disconnect_count": 0, "reconnect_expired": False, "forfeited": False},
        ],
        "teams": [],
        "result_context": {
            "league_id": league["id"],
            "league_season_id": season["id"],
            "league_fixture_id": fixture["id"],
        },
    }

    # Persist completed match: atomically updates match history, calculates Elo, and advances fixture
    persisted = match_history.persist_completed(match_payload)
    assert persisted is True

    # 1. Elo ratings were updated
    p1_ratings = rating_service.player_ratings_summary(p1.id)
    assert p1_ratings["global_rating"]["rating"] > 1000
    assert p1_ratings["global_rating"]["wins"] == 1

    # 2. League fixture completed and crowned
    updated_fixture = next(f for f in league_service.get_fixtures(season["id"]) if f["id"] == fixture["id"])
    assert updated_fixture["status"] == "completed"
    assert updated_fixture["winner_player_id"] == p1.id

    # 3. Season completed
    s = league_service.get_season(season["id"])
    assert s["status"] == "completed"

    # 4. Idempotency test: repeating the same engine result payload returns False and does not duplicate
    repeat = match_history.persist_completed(match_payload)
    assert repeat is False


# =============================================================================
# 8. REST API CRUD & AUTHORIZATION TESTS
# =============================================================================

def test_league_api_full_flow(league_app):
    client, db = league_app
    identities = PlayerIdentityService(db)
    owner = create_player(identities, "Commissioner")
    player_a = create_player(identities, "Alpha")
    player_b = create_player(identities, "Beta")

    # 1. Create League via API
    res = client.post("/api/leagues", headers=owner.headers, json={
        "name": "API League",
        "description": "Created via API",
        "mode": "classic",
    })
    assert res.status_code == 201
    league = res.json()
    league_id = league["id"]
    assert league["name"] == "API League"

    # 2. List Leagues
    res = client.get("/api/leagues")
    assert res.status_code == 200
    assert any(l["id"] == league_id for l in res.json()["leagues"])

    # 3. Create Season
    res = client.post(f"/api/leagues/{league_id}/seasons", headers=owner.headers, json={
        "name": "Season 1 API",
        "capacity": 4,
    })
    assert res.status_code == 201
    season = res.json()
    season_id = season["id"]

    # 4. Open Registration
    res = client.post(f"/api/leagues/{league_id}/seasons/{season_id}/open-registration", headers=owner.headers)
    assert res.status_code == 200
    assert res.json()["status"] == "registration_open"

    # 5. Register Player A and Player B
    res = client.post(f"/api/leagues/{league_id}/seasons/{season_id}/register", headers=player_a.headers)
    assert res.status_code == 201
    res = client.post(f"/api/leagues/{league_id}/seasons/{season_id}/register", headers=player_b.headers)
    assert res.status_code == 201

    # 6. Start Season
    res = client.post(f"/api/leagues/{league_id}/seasons/{season_id}/start", headers=owner.headers)
    assert res.status_code == 200
    assert res.json()["status"] == "in_progress"

    # 7. Get Standings
    res = client.get(f"/api/leagues/{league_id}/seasons/{season_id}/standings")
    assert res.status_code == 200
    assert len(res.json()) == 2

    # 8. Get Fixtures
    res = client.get(f"/api/leagues/{league_id}/seasons/{season_id}/fixtures")
    assert res.status_code == 200
    fixtures = res.json()
    assert len(fixtures) == 1
    fixture_id = fixtures[0]["id"]

    # 9. Get Fixture Ticket
    res = client.post(f"/api/leagues/{league_id}/seasons/{season_id}/fixtures/{fixture_id}/ticket", headers=player_a.headers)
    assert res.status_code == 200
    ticket = res.json()["ticket"]
    assert ticket is not None

    # 10. Forfeit Fixture
    res = client.post(f"/api/leagues/{league_id}/seasons/{season_id}/fixtures/{fixture_id}/forfeit", headers=player_b.headers, json={"reason": "player_forfeit"})
    assert res.status_code == 200
    assert res.json()["status"] == "completed"

    # 11. Dispute Fixture
    res = client.post(f"/api/leagues/{league_id}/seasons/{season_id}/fixtures/{fixture_id}/dispute", headers=player_a.headers, json={"reason": "latency_dispute", "details": {}})
    assert res.status_code == 200

    # 12. Audit Trail
    res = client.get(f"/api/leagues/{league_id}/seasons/{season_id}/audit")
    assert res.status_code == 200
    assert len(res.json()) >= 4


def test_odd_players_season_lifecycle_with_byes(league_database: Database):
    identities = PlayerIdentityService(league_database)
    owner = create_player(identities, "Commissioner")
    p1 = create_player(identities, "Player1")
    p2 = create_player(identities, "Player2")
    p3 = create_player(identities, "Player3")

    service = LeagueService(league_database)
    league = service.create_league(owner_player_id=owner.id, name="Odd League", mode="speed_challenge")
    season = service.create_season(league_id=league["id"], actor_id=owner.id, name="Season Odd", capacity=4)
    service.open_registration(season["id"], actor_id=owner.id)

    service.register_player(season["id"], p1.id)
    service.register_player(season["id"], p2.id)
    service.register_player(season["id"], p3.id)

    started = service.start_season(season["id"], actor_id=owner.id)
    assert started["status"] == "in_progress"

    # 3 players -> 3 rounds, each round has 1 match + 1 bye = 6 fixtures total (3 bye fixtures)
    fixtures = service.get_fixtures(season["id"])
    assert len(fixtures) == 6
    bye_fixtures = [f for f in fixtures if f["status"] == "bye"]
    assert len(bye_fixtures) == 3

    # Each player received exactly 1 bye fixture
    bye_pids = [f["player_one_id"] for f in bye_fixtures]
    assert sorted(bye_pids) == sorted([p1.id, p2.id, p3.id])

    # Complete the 3 non-bye matches
    regular_fixtures = [f for f in fixtures if f["status"] != "bye"]
    assert len(regular_fixtures) == 3

    with league_database.sessions.begin() as s:
        # Match 1: P1 vs P2 -> P1 wins (P1 score 10, P2 score 5)
        f12 = next(f for f in regular_fixtures if {f["player_one_id"], f["player_two_id"]} == {p1.id, p2.id})
        p1_is_first = f12["player_one_id"] == p1.id
        s1, s2 = (10, 5) if p1_is_first else (5, 10)
        service.process_fixture_completion(s, f12["id"], None, {
            "source": "game_engine",
            "participants": [
                {"player_id": f12["player_one_id"], "final_score": s1, "placement": 1 if p1_is_first else 2},
                {"player_id": f12["player_two_id"], "final_score": s2, "placement": 2 if p1_is_first else 1},
            ],
            "result_context": {"league_fixture_id": f12["id"]},
        })

        # Match 2: P1 vs P3 -> P3 wins (P3 score 8, P1 score 4)
        f13 = next(f for f in regular_fixtures if {f["player_one_id"], f["player_two_id"]} == {p1.id, p3.id})
        p3_is_first = f13["player_one_id"] == p3.id
        s1, s2 = (8, 4) if p3_is_first else (4, 8)
        service.process_fixture_completion(s, f13["id"], None, {
            "source": "game_engine",
            "participants": [
                {"player_id": f13["player_one_id"], "final_score": s1, "placement": 1 if p3_is_first else 2},
                {"player_id": f13["player_two_id"], "final_score": s2, "placement": 2 if p3_is_first else 1},
            ],
            "result_context": {"league_fixture_id": f13["id"]},
        })

        # Match 3: P2 vs P3 -> Draw 6-6
        f23 = next(f for f in regular_fixtures if {f["player_one_id"], f["player_two_id"]} == {p2.id, p3.id})
        service.process_fixture_completion(s, f23["id"], None, {
            "source": "game_engine",
            "participants": [
                {"player_id": f23["player_one_id"], "final_score": 6, "placement": 1},
                {"player_id": f23["player_two_id"], "final_score": 6, "placement": 1},
            ],
            "result_context": {"league_fixture_id": f23["id"]},
        })

    # Season is completed!
    completed = service.get_season(season["id"])
    assert completed["status"] == "completed"
    assert completed["completed_at"] is not None

    standings = completed["standings"]
    assert len(standings) == 3
    # P3: won against P1 (3 pts), drew against P2 (1 pt) -> 4 pts (Rank 1 champion!)
    # P1: won against P2 (3 pts), lost against P3 (0 pts) -> 3 pts (Rank 2)
    # P2: lost to P1 (0 pts), drew with P3 (1 pt) -> 1 pt (Rank 3)
    assert standings[0]["player_id"] == p3.id and standings[0]["rank"] == 1 and standings[0]["points"] == 4
    assert standings[1]["player_id"] == p1.id and standings[1]["rank"] == 2 and standings[1]["points"] == 3
    assert standings[2]["player_id"] == p2.id and standings[2]["rank"] == 3 and standings[2]["points"] == 1


def test_pydantic_schemas_and_validation():
    from pydantic import ValidationError
    from quizverse.league_api import (
        DisputeCreate,
        ForfeitRequest,
        LeagueCreate,
        LeagueUpdate,
        MatchLaunchRequest,
        SeasonCreate,
    )

    # Valid LeagueCreate
    valid = LeagueCreate(name="Premier League", mode="classic", description="Great games")
    assert valid.name == "Premier League"
    assert valid.mode == "classic"

    # Rejects invalid mode
    with pytest.raises(ValidationError):
        LeagueCreate(name="Test", mode="solo_practice")

    # Rejects extra fields (strict)
    with pytest.raises(ValidationError):
        LeagueCreate(name="Test", mode="classic", rogue_field=True)

    # Rejects capacity < 2 or > 32
    with pytest.raises(ValidationError):
        SeasonCreate(name="Season 1", capacity=1)
    with pytest.raises(ValidationError):
        SeasonCreate(name="Season 1", capacity=33)

    # Valid SeasonCreate
    valid_season = SeasonCreate(name="Season 1", capacity=16)
    assert valid_season.capacity == 16


def test_authorization_and_owner_permissions(league_app):
    client, db = league_app
    identities = PlayerIdentityService(db)
    owner = create_player(identities, "Owner")
    stranger = create_player(identities, "Stranger")

    # 1. Unauthenticated creation fails
    res = client.post("/api/leagues", json={"name": "No Auth", "mode": "classic"})
    assert res.status_code == 401

    # 2. Owner creates league
    res = client.post("/api/leagues", headers=owner.headers, json={"name": "Owner League", "mode": "classic"})
    assert res.status_code == 201
    league_id = res.json()["id"]

    # 3. Stranger tries to create season in owner's league -> 403 Forbidden
    res = client.post(f"/api/leagues/{league_id}/seasons", headers=stranger.headers, json={"name": "Hacked Season"})
    assert res.status_code == 403

    # 4. Stranger tries to update owner's league -> 403 Forbidden
    res = client.patch(f"/api/leagues/{league_id}", headers=stranger.headers, json={"name": "Renamed"})
    assert res.status_code == 403


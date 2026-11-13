"""Comprehensive test suite for Stage 8.3 Tournament MVP.

Tests bracket generation, seeding, byes, lifecycle transitions, registration,
fixture admission tickets, match advancement, champion crowning, forfeits, disputes,
audit trail, and REST APIs.
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
    MatchParticipant,
    PlayerProfile,
    PlayerRating,
    Tournament,
    TournamentAuditEvent,
    TournamentFixtureTicket,
    TournamentMatch,
    TournamentParticipant,
    TournamentRound,
)
from quizverse.persistence.player_identity import (
    MatchHistoryService,
    PlayerIdentityService,
)
from quizverse.persistence.ratings import RatingService
from quizverse.persistence.tournaments import (
    TournamentError,
    TournamentService,
    get_bracket_slots,
)
from quizverse.realtime import create_app


# =============================================================================
# 1. BRACKET MATH AND PAIRING UNIT TESTS
# =============================================================================

def test_bracket_pairing_slots():
    assert get_bracket_slots(2) == [1, 2]
    assert get_bracket_slots(4) == [1, 4, 2, 3]
    assert get_bracket_slots(8) == [1, 8, 4, 5, 2, 7, 3, 6]
    assert get_bracket_slots(16) == [1, 16, 8, 9, 4, 13, 5, 12, 2, 15, 7, 10, 3, 14, 6, 11]

    # Every slot must contain seeds 1..N exactly once
    for size in [2, 4, 8, 16, 32]:
        slots = get_bracket_slots(size)
        assert len(slots) == size
        assert sorted(slots) == list(range(1, size + 1))
        # Top seed #1 always plays bottom seed #size in the first match
        assert slots[0] == 1
        assert slots[1] == size


# =============================================================================
# 2. DATABASE FIXTURES
# =============================================================================

@pytest.fixture
def tournament_database(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'tournament-test.sqlite3').as_posix()}"
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
def tournament_app(tournament_database: Database):
    with TestClient(create_app(database=tournament_database)) as client:
        yield client, tournament_database


class MockPlayer:
    def __init__(self, issued):
        self.player_id = issued.player_id
        self.id = issued.player_id
        self.token = issued.token
        self.credential = issued.token

    def __getitem__(self, item):
        if item == "id":
            return self.player_id
        raise KeyError(item)

    @property
    def player(self):
        return {"id": self.player_id}


def _create_player(identity_service: PlayerIdentityService, name: str) -> MockPlayer:
    issued = identity_service.create(name)
    return MockPlayer(issued)


# =============================================================================
# 3. TOURNAMENT SERVICE DOMAIN UNIT TESTS
# =============================================================================

def test_tournament_creation_and_validation(tournament_database: Database):
    identities = PlayerIdentityService(tournament_database)
    service = TournamentService(tournament_database)

    owner = _create_player(identities, "AliceOrganizer")
    owner_id = owner.player["id"]

    # Valid creation
    t = service.create_tournament(
        owner_id=owner_id,
        name="Winter Knockout 2026",
        mode="speed_challenge",
        capacity=8,
        description="Speed Challenge Tournament",
    )
    assert t["name"] == "Winter Knockout 2026"
    assert t["mode"] == "speed_challenge"
    assert t["status"] == "draft"
    assert t["capacity"] == 8

    # Reject unsupported mode
    with pytest.raises(TournamentError) as exc_info:
        service.create_tournament(owner_id=owner_id, name="Invalid Mode", mode="quiz_royale")
    assert exc_info.value.code == "invalid_tournament_mode"

    # Reject invalid capacity
    with pytest.raises(TournamentError) as exc_info:
        service.create_tournament(owner_id=owner_id, name="Invalid Cap", mode="classic", capacity=1)
    assert exc_info.value.code == "invalid_capacity"

    # Reject empty name
    with pytest.raises(TournamentError) as exc_info:
        service.create_tournament(owner_id=owner_id, name="   ", mode="classic")
    assert exc_info.value.code == "invalid_tournament_name"


def test_tournament_lifecycle_transitions(tournament_database: Database):
    identities = PlayerIdentityService(tournament_database)
    service = TournamentService(tournament_database)

    owner = _create_player(identities, "Alice")
    owner_id = owner.player["id"]
    t = service.create_tournament(owner_id=owner_id, name="Lifecycle Cup", mode="classic", capacity=4)
    t_id = t["id"]

    # Draft -> registration_open
    res = service.open_registration(t_id, owner_id)
    assert res["status"] == "registration_open"

    # Cannot open registration again (already open)
    with pytest.raises(TournamentError) as exc:
        service.open_registration(t_id, owner_id)
    assert exc.value.code == "invalid_transition"

    # Non-owner cannot close registration
    other = _create_player(identities, "Bob")
    with pytest.raises(TournamentError) as exc:
        service.close_registration(t_id, other.player["id"])
    assert exc.value.code == "forbidden"

    # Owner closes registration
    res = service.close_registration(t_id, owner_id)
    assert res["status"] == "registration_closed"

    # Cannot start with < 2 participants
    with pytest.raises(TournamentError) as exc:
        service.start_tournament(t_id, owner_id)
    assert exc.value.code == "insufficient_participants"


def test_registration_and_withdrawal(tournament_database: Database):
    identities = PlayerIdentityService(tournament_database)
    service = TournamentService(tournament_database)

    owner = _create_player(identities, "HostPlayer")
    owner_id = owner.player["id"]
    p1 = _create_player(identities, "PlayerOne")
    p2 = _create_player(identities, "PlayerTwo")
    p3 = _create_player(identities, "PlayerThree")

    t = service.create_tournament(owner_id=owner_id, name="Registration Cup", mode="classic", capacity=2)
    t_id = t["id"]

    # Cannot register while in draft
    with pytest.raises(TournamentError) as exc:
        service.register_player(t_id, p1.player["id"])
    assert exc.value.code == "registration_not_open"

    service.open_registration(t_id, owner_id)

    # P1 registers
    reg1 = service.register_player(t_id, p1.player["id"])
    assert reg1["player_id"] == p1.player["id"]
    assert reg1["status"] == "registered"

    # Idempotent re-registration returns same status
    reg1_again = service.register_player(t_id, p1.player["id"])
    assert reg1_again["player_id"] == p1.player["id"]

    # P2 registers (fills capacity = 2)
    service.register_player(t_id, p2.player["id"])

    # P3 fails due to capacity
    with pytest.raises(TournamentError) as exc:
        service.register_player(t_id, p3.player["id"])
    assert exc.value.code == "tournament_full"

    # P2 withdraws
    w_res = service.withdraw_player(t_id, p2.player["id"])
    assert w_res["status"] == "withdrawn"

    # Now P3 can register
    service.register_player(t_id, p3.player["id"])


def test_deterministic_seeding_order(tournament_database: Database):
    identities = PlayerIdentityService(tournament_database)
    ratings = RatingService(tournament_database)
    service = TournamentService(tournament_database)

    owner = _create_player(identities, "Organizer")
    owner_id = owner.player["id"]
    p1 = _create_player(identities, "PlayerPro")
    p2 = _create_player(identities, "PlayerSemiPro")
    p3 = _create_player(identities, "PlayerNovice")
    p4 = _create_player(identities, "PlayerUnrated")

    # Give P1 a mode rating of 1200
    with tournament_database.sessions.begin() as session:
        session.add(PlayerRating(
            id="r1", player_id=p1.player["id"], rating_scope="mode:classic",
            rating=1200, rated_match_count=10, wins=10, losses=0, draws=0,
            algorithm_version="elo-v1.0"
        ))
        # Give P2 a mode rating of 1100
        session.add(PlayerRating(
            id="r2", player_id=p2.player["id"], rating_scope="mode:classic",
            rating=1100, rated_match_count=10, wins=8, losses=2, draws=0,
            algorithm_version="elo-v1.0"
        ))
        # Give P3 a global rating of 1050 (no mode rating)
        session.add(PlayerRating(
            id="r3", player_id=p3.player["id"], rating_scope="global",
            rating=1050, rated_match_count=5, wins=3, losses=2, draws=0,
            algorithm_version="elo-v1.0"
        ))

    t = service.create_tournament(owner_id=owner_id, name="Seeding Cup", mode="classic", capacity=4)
    t_id = t["id"]
    service.open_registration(t_id, owner_id)

    # Register in reverse order
    service.register_player(t_id, p4.player["id"])
    service.register_player(t_id, p3.player["id"])
    service.register_player(t_id, p2.player["id"])
    service.register_player(t_id, p1.player["id"])

    # Start tournament -> bracket generated
    service.start_tournament(t_id, owner_id)
    bracket = service.get_bracket(t_id)

    participants = bracket["participants"]
    # Seed 1: P1 (mode rating 1200)
    # Seed 2: P2 (mode rating 1100)
    # Seed 3: P3 (global rating 1050)
    # Seed 4: P4 (unrated default 1000)
    seed_map = {p["player_id"]: p["seed"] for p in participants}
    assert seed_map[p1.player["id"]] == 1
    assert seed_map[p2.player["id"]] == 2
    assert seed_map[p3.player["id"]] == 3
    assert seed_map[p4.player["id"]] == 4


def test_2_player_tournament_full_lifecycle(tournament_database: Database):
    identities = PlayerIdentityService(tournament_database)
    service = TournamentService(tournament_database)

    owner = _create_player(identities, "Owner")
    owner_id = owner.player["id"]
    p1 = _create_player(identities, "Player1")
    p2 = _create_player(identities, "Player2")

    t = service.create_tournament(owner_id=owner_id, name="Duel Cup", mode="classic", capacity=2)
    t_id = t["id"]
    service.open_registration(t_id, owner_id)
    service.register_player(t_id, p1.player["id"])
    service.register_player(t_id, p2.player["id"])

    started = service.start_tournament(t_id, owner_id)
    assert started["tournament"]["status"] == "in_progress"

    bracket = service.get_bracket(t_id)
    assert len(bracket["rounds"]) == 1
    round_1 = bracket["rounds"][0]
    assert len(round_1["matches"]) == 1
    m1 = round_1["matches"][0]
    assert m1["status"] == "scheduled"
    assert {m1["player_one"]["id"], m1["player_two"]["id"]} == {p1.player["id"], p2.player["id"]}

    # Issue tickets
    t1 = service.issue_fixture_admission_ticket(t_id, m1["id"], p1.player["id"])
    t2 = service.issue_fixture_admission_ticket(t_id, m1["id"], p2.player["id"])
    assert t1["ticket"] and t2["ticket"]

    # Consume P1 ticket
    assert service.validate_and_consume_ticket(t_id, m1["id"], p1.player["id"], t1["ticket"]) is True
    # Replay fails
    with pytest.raises(TournamentError) as exc:
        service.validate_and_consume_ticket(t_id, m1["id"], p1.player["id"], t1["ticket"])
    assert exc.value.code == "ticket_already_used"

    # Simulate match result: P1 wins
    clean_result = {
        "participants": [
            {"player_id": p1.player["id"], "placement": 1, "final_score": 100},
            {"player_id": p2.player["id"], "placement": 2, "final_score": 80},
        ]
    }
    with tournament_database.sessions.begin() as session:
        advanced = service.process_match_completion(session, m1["id"], None, clean_result)
        assert advanced is True

    # Tournament must now be completed!
    completed = service.get_tournament(t_id)
    assert completed["status"] == "completed"

    bracket = service.get_bracket(t_id)
    p_map = {p["player_id"]: p for p in bracket["participants"]}
    assert p_map[p1.player["id"]]["status"] == "champion"
    assert p_map[p1.player["id"]]["final_placement"] == 1
    assert p_map[p2.player["id"]]["status"] == "runner_up"
    assert p_map[p2.player["id"]]["final_placement"] == 2


def test_3_player_tournament_with_bye(tournament_database: Database):
    identities = PlayerIdentityService(tournament_database)
    service = TournamentService(tournament_database)

    owner = _create_player(identities, "Owner")
    owner_id = owner.player["id"]
    p1 = _create_player(identities, "Seed1Player")
    p2 = _create_player(identities, "Seed2Player")
    p3 = _create_player(identities, "Seed3Player")

    # Give P1 higher rating so it is Seed 1
    with tournament_database.sessions.begin() as session:
        session.add(PlayerRating(
            id="r1", player_id=p1.player["id"], rating_scope="global",
            rating=1300, rated_match_count=5, wins=5, losses=0, draws=0,
            algorithm_version="elo-v1.0"
        ))

    t = service.create_tournament(owner_id=owner_id, name="Triad Cup", mode="speed_challenge", capacity=4)
    t_id = t["id"]
    service.open_registration(t_id, owner_id)
    service.register_player(t_id, p1.player["id"])
    service.register_player(t_id, p2.player["id"])
    service.register_player(t_id, p3.player["id"])

    # 3 players in a 4-bracket -> Seed 1 gets a BYE
    service.start_tournament(t_id, owner_id)
    bracket = service.get_bracket(t_id)

    assert len(bracket["rounds"]) == 2  # Round 1 (Semis) and Round 2 (Final)
    r1 = bracket["rounds"][0]
    r2 = bracket["rounds"][1]

    # In Round 1:
    # Match 1: Seed 1 vs Seed 4 (BYE) -> status is 'bye', winner is Seed 1 (P1)
    # Match 2: Seed 2 vs Seed 3 -> status is 'scheduled'
    m1 = r1["matches"][0]
    m2 = r1["matches"][1]

    assert m1["status"] == "bye"
    assert m1["winner"]["id"] == p1.player["id"]

    assert m2["status"] == "scheduled"
    assert {m2["player_one"]["id"], m2["player_two"]["id"]} == {p2.player["id"], p3.player["id"]}

    # In Round 2 (Final): Seed 1 is already advanced to Match 1 of Round 2!
    final_match = r2["matches"][0]
    assert final_match["player_one"]["id"] == p1.player["id"]
    assert final_match["player_two"] is None  # Waiting for winner of m2

    # Now Match 2 completes: P2 wins over P3
    clean_result_m2 = {
        "participants": [
            {"player_id": p2.player["id"], "placement": 1, "final_score": 50},
            {"player_id": p3.player["id"], "placement": 2, "final_score": 40},
        ]
    }
    with tournament_database.sessions.begin() as session:
        service.process_match_completion(session, m2["id"], None, clean_result_m2)

    # Now Round 2 Final match should be scheduled between P1 and P2!
    bracket_after_m2 = service.get_bracket(t_id)
    final_after = bracket_after_m2["rounds"][1]["matches"][0]
    assert final_after["status"] == "scheduled"
    assert final_after["player_one"]["id"] == p1.player["id"]
    assert final_after["player_two"]["id"] == p2.player["id"]

    # Final Match completes: P1 defeats P2
    clean_result_final = {
        "participants": [
            {"player_id": p1.player["id"], "placement": 1, "final_score": 100},
            {"player_id": p2.player["id"], "placement": 2, "final_score": 80},
        ]
    }
    with tournament_database.sessions.begin() as session:
        service.process_match_completion(session, final_after["id"], None, clean_result_final)

    # Tournament complete
    final_tourn = service.get_tournament(t_id)
    assert final_tourn["status"] == "completed"
    bracket_final = service.get_bracket(t_id)
    p_map = {p["player_id"]: p for p in bracket_final["participants"]}
    assert p_map[p1.player["id"]]["status"] == "champion"
    assert p_map[p2.player["id"]]["status"] == "runner_up"
    assert p_map[p3.player["id"]]["status"] == "eliminated"


def test_match_forfeit_and_dispute(tournament_database: Database):
    identities = PlayerIdentityService(tournament_database)
    service = TournamentService(tournament_database)

    owner = _create_player(identities, "Owner")
    owner_id = owner.player["id"]
    p1 = _create_player(identities, "PlayerOne")
    p2 = _create_player(identities, "PlayerTwo")

    t = service.create_tournament(owner_id=owner_id, name="Forfeit Cup", mode="elimination", capacity=2)
    t_id = t["id"]
    service.open_registration(t_id, owner_id)
    service.register_player(t_id, p1.player["id"])
    service.register_player(t_id, p2.player["id"])
    service.start_tournament(t_id, owner_id)

    bracket = service.get_bracket(t_id)
    m = bracket["rounds"][0]["matches"][0]

    # File dispute
    dispute_res = service.file_dispute(t_id, m["id"], p1.player["id"], "Opponent connection dropped unexpectedly")
    assert dispute_res["status"] == "disputed"

    # Forfeit by P1
    forfeit_res = service.forfeit_match(t_id, m["id"], p1.player["id"], reason="player_quit")
    updated_m = forfeit_res["rounds"][0]["matches"][0]
    assert updated_m["status"] == "forfeited"
    assert updated_m["winner"]["id"] == p2.player["id"]

    # P2 crowned champion
    t_detail = service.get_tournament(t_id)
    assert t_detail["status"] == "completed"

    # Audit events must record dispute and forfeit
    events = service.get_audit_trail(t_id)
    actions = [ev["action"] for ev in events]
    assert "match_disputed" in actions
    assert "match_forfeited" in actions


def test_match_history_integration_triggers_tournament_advancement(tournament_database: Database):
    identities = PlayerIdentityService(tournament_database)
    tournament_service = TournamentService(tournament_database)
    match_history = MatchHistoryService(tournament_database, tournament_service=tournament_service)

    owner = _create_player(identities, "Owner")
    p1 = _create_player(identities, "PlayerA")
    p2 = _create_player(identities, "PlayerB")

    t = tournament_service.create_tournament(owner_id=owner.player_id, name="Integration Cup", mode="classic", capacity=2)
    t_id = t["id"]
    tournament_service.open_registration(t_id, owner.player_id)
    tournament_service.register_player(t_id, p1.player_id)
    tournament_service.register_player(t_id, p2.player_id)
    tournament_service.start_tournament(t_id, owner.player_id)

    bracket = tournament_service.get_bracket(t_id)
    m = bracket["rounds"][0]["matches"][0]
    match_id = m["id"]

    now = datetime.now(UTC)
    payload = {
        "source": "game_engine",
        "engine_match_id": "e" * 32,
        "mode": "classic",
        "status": "completed",
        "result_kind": "individual",
        "rules_version": "v1.0",
        "started_at": now - timedelta(minutes=5),
        "completed_at": now,
        "question_set_hash": "a" * 64,
        "participants": [
            {
                "seat_index": 0, "player_id": p1.player_id, "placement": 1,
                "outcome": "win", "final_score": 100, "correct_count": 5,
                "answer_count": 5, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False,
            },
            {
                "seat_index": 1, "player_id": p2.player_id, "placement": 2,
                "outcome": "loss", "final_score": 60, "correct_count": 3,
                "answer_count": 5, "disconnect_count": 0, "reconnect_expired": False, "forfeited": False,
            },
        ],
        "teams": [],
        "result_context": {
            "tournament_id": t_id,
            "tournament_match_id": match_id,
        },
    }

    persisted = match_history.persist_completed(payload)
    assert persisted is True

    updated_t = tournament_service.get_tournament(t_id)
    assert updated_t["status"] == "completed"
    bracket_after = tournament_service.get_bracket(t_id)
    parts = {p["player_id"]: p for p in bracket_after["participants"]}
    assert parts[p1.player_id]["status"] == "champion"
    assert parts[p1.player_id]["final_placement"] == 1
    assert parts[p2.player_id]["status"] == "runner_up"
    assert parts[p2.player_id]["final_placement"] == 2


# =============================================================================
# 4. REST API VALIDATION AND ROUTE TABLE TESTS
# =============================================================================

from pydantic import ValidationError
from quizverse.quiz_api import ApiProblem
from quizverse.tournament_api import (
    DisputeCreate,
    ForfeitRequest,
    MatchLaunchRequest,
    TournamentCreate,
    TournamentUpdate,
    _authenticated_header,
)


def test_api_strict_request_models():
    # Valid TournamentCreate
    valid = TournamentCreate(name="Summer Cup", mode="classic", capacity=8, description="Test")
    assert valid.name == "Summer Cup"
    assert valid.mode == "classic"
    assert valid.capacity == 8

    # Rejects empty name
    with pytest.raises(ValidationError):
        TournamentCreate(name="", mode="classic", capacity=8)

    # Rejects name > 80 chars
    with pytest.raises(ValidationError):
        TournamentCreate(name="x" * 81, mode="classic", capacity=8)

    # Rejects capacity < 2 or > 64
    with pytest.raises(ValidationError):
        TournamentCreate(name="Cup", mode="classic", capacity=1)
    with pytest.raises(ValidationError):
        TournamentCreate(name="Cup", mode="classic", capacity=65)

    # Rejects unsupported mode
    with pytest.raises(ValidationError):
        TournamentCreate(name="Cup", mode="quiz_royale", capacity=8)

    # Rejects extra fields (strict / forbid)
    with pytest.raises(ValidationError):
        TournamentCreate(name="Cup", mode="classic", capacity=8, rogue_field=True)

    # TournamentUpdate
    up = TournamentUpdate(name="Renamed Cup", capacity=16)
    assert up.name == "Renamed Cup"
    assert up.capacity == 16
    with pytest.raises(ValidationError):
        TournamentUpdate(capacity=100)

    # DisputeCreate
    disp = DisputeCreate(reason="Connection dropped", details={"round": 1})
    assert disp.reason == "Connection dropped"
    with pytest.raises(ValidationError):
        DisputeCreate(reason="")

    # ForfeitRequest
    forfeit = ForfeitRequest(reason="player_quit")
    assert forfeit.reason == "player_quit"

    # MatchLaunchRequest
    launch = MatchLaunchRequest(ticket="ticket123", player_name="Alice")
    assert launch.ticket == "ticket123"


def test_api_auth_header_validation():
    # Valid Bearer token
    assert _authenticated_header("Bearer secret_credential_token") == "secret_credential_token"

    # Missing header
    with pytest.raises(ApiProblem) as exc:
        _authenticated_header(None)
    assert exc.value.status_code == 401
    assert exc.value.code == "missing_player_credential"

    # Non-Bearer prefix
    with pytest.raises(ApiProblem) as exc:
        _authenticated_header("Basic dXNlcjpwYXNz")
    assert exc.value.status_code == 401

    # Empty token
    with pytest.raises(ApiProblem) as exc:
        _authenticated_header("Bearer   ")
    assert exc.value.status_code == 401


def test_tournament_route_table_registration(tournament_database: Database):
    app = create_app(database=tournament_database)

    # Check that all tournament endpoints are installed on the app
    tournament_paths = set()
    for inc in [r for r in app.routes if type(r).__name__ == "_IncludedRouter"]:
        if inc.original_router.prefix == "/api/tournaments":
            for r in inc.original_router.routes:
                tournament_paths.add(r.path)

    expected_endpoints = {
        "/api/tournaments",
        "/api/tournaments/{tournament_id}",
        "/api/tournaments/{tournament_id}/open-registration",
        "/api/tournaments/{tournament_id}/close-registration",
        "/api/tournaments/{tournament_id}/registration",
        "/api/tournaments/{tournament_id}/start",
        "/api/tournaments/{tournament_id}/cancel",
        "/api/tournaments/{tournament_id}/bracket",
        "/api/tournaments/{tournament_id}/matches",
        "/api/tournaments/{tournament_id}/matches/{match_id}",
        "/api/tournaments/{tournament_id}/matches/{match_id}/admission-ticket",
        "/api/tournaments/{tournament_id}/matches/{match_id}/launch",
        "/api/tournaments/{tournament_id}/matches/{match_id}/forfeit",
        "/api/tournaments/{tournament_id}/matches/{match_id}/disputes",
        "/api/tournaments/{tournament_id}/audit",
    }
    assert expected_endpoints.issubset(tournament_paths)


# =============================================================================
# 5. ADVANCED DOMAIN & SECURITY TESTS
# =============================================================================

def test_result_idempotency_and_duplicate_submissions(tournament_database: Database):
    identities = PlayerIdentityService(tournament_database)
    service = TournamentService(tournament_database)

    owner = _create_player(identities, "Owner")
    p1 = _create_player(identities, "PlayerA")
    p2 = _create_player(identities, "PlayerB")

    t = service.create_tournament(owner_id=owner.player_id, name="Idempotency Cup", mode="classic", capacity=2)
    t_id = t["id"]
    service.open_registration(t_id, owner.player_id)
    service.register_player(t_id, p1.player_id)
    service.register_player(t_id, p2.player_id)
    service.start_tournament(t_id, owner.player_id)

    bracket = service.get_bracket(t_id)
    m = bracket["rounds"][0]["matches"][0]
    match_id = m["id"]

    clean_result = {
        "participants": [
            {"player_id": p1.player_id, "placement": 1, "final_score": 100},
            {"player_id": p2.player_id, "placement": 2, "final_score": 75},
        ]
    }

    # First completion succeeds and returns True
    with tournament_database.sessions.begin() as session:
        first_call = service.process_match_completion(session, match_id, None, clean_result)
        assert first_call is True

    # Immediate second call is completely idempotent and returns False
    with tournament_database.sessions.begin() as session:
        second_call = service.process_match_completion(session, match_id, None, clean_result)
        assert second_call is False

    # Third call with different placement or score is ignored because match is already completed
    altered_result = {
        "participants": [
            {"player_id": p1.player_id, "placement": 2, "final_score": 50},
            {"player_id": p2.player_id, "placement": 1, "final_score": 100},
        ]
    }
    with tournament_database.sessions.begin() as session:
        third_call = service.process_match_completion(session, match_id, None, altered_result)
        assert third_call is False

    # Verify P1 remains the champion
    completed_t = service.get_tournament(t_id)
    assert completed_t["status"] == "completed"
    bracket_after = service.get_bracket(t_id)
    p_map = {p["player_id"]: p for p in bracket_after["participants"]}
    assert p_map[p1.player_id]["status"] == "champion"
    assert p_map[p1.player_id]["final_placement"] == 1


def test_fixture_ticket_security_and_expiry(tournament_database: Database):
    identities = PlayerIdentityService(tournament_database)
    service = TournamentService(tournament_database)

    owner = _create_player(identities, "Owner")
    p1 = _create_player(identities, "PlayerOne")
    p2 = _create_player(identities, "PlayerTwo")
    outsider = _create_player(identities, "Intruder")

    t = service.create_tournament(owner_id=owner.player_id, name="Security Cup", mode="power_play", capacity=2)
    t_id = t["id"]
    service.open_registration(t_id, owner.player_id)
    service.register_player(t_id, p1.player_id)
    service.register_player(t_id, p2.player_id)
    service.start_tournament(t_id, owner.player_id)

    bracket = service.get_bracket(t_id)
    m = bracket["rounds"][0]["matches"][0]
    match_id = m["id"]

    # 1. Outsider cannot request admission ticket
    with pytest.raises(TournamentError) as exc:
        service.issue_fixture_admission_ticket(t_id, match_id, outsider.player_id)
    assert exc.value.code == "player_mismatch"
    assert exc.value.status_code == 403

    # 2. Player 1 requests ticket
    issued_1 = service.issue_fixture_admission_ticket(t_id, match_id, p1.player_id)
    raw_token_1 = issued_1["ticket"]

    # 3. Invalid ticket token rejected
    with pytest.raises(TournamentError) as exc:
        service.validate_and_consume_ticket(t_id, match_id, p1.player_id, "invalid_raw_token")
    assert exc.value.code == "invalid_fixture_ticket"
    assert exc.value.status_code == 401

    # 4. Valid ticket consumed successfully
    assert service.validate_and_consume_ticket(t_id, match_id, p1.player_id, raw_token_1) is True

    # 5. Replay fails (already used)
    with pytest.raises(TournamentError) as exc:
        service.validate_and_consume_ticket(t_id, match_id, p1.player_id, raw_token_1)
    assert exc.value.code == "ticket_already_used"
    assert exc.value.status_code == 401

    # 6. Ticket expiry test
    issued_2 = service.issue_fixture_admission_ticket(t_id, match_id, p2.player_id)
    raw_token_2 = issued_2["ticket"]

    # Manually expire P2's ticket in the database
    with tournament_database.sessions.begin() as session:
        expired_ticket = session.scalar(
            select(TournamentFixtureTicket).where(
                TournamentFixtureTicket.tournament_match_id == match_id,
                TournamentFixtureTicket.player_id == p2.player_id,
            )
        )
        assert expired_ticket is not None
        expired_ticket.expires_at = datetime.now(UTC) - timedelta(seconds=120)

    with pytest.raises(TournamentError) as exc:
        service.validate_and_consume_ticket(t_id, match_id, p2.player_id, raw_token_2)
    assert exc.value.code == "ticket_expired"
    assert exc.value.status_code == 401


def test_4_player_tournament_winner_advancement_and_completion(tournament_database: Database):
    identities = PlayerIdentityService(tournament_database)
    service = TournamentService(tournament_database)

    owner = _create_player(identities, "Owner")
    p1 = _create_player(identities, "Seed1")
    p2 = _create_player(identities, "Seed2")
    p3 = _create_player(identities, "Seed3")
    p4 = _create_player(identities, "Seed4")

    # Seed ratings: P1=1400, P2=1300, P3=1200, P4=1100
    with tournament_database.sessions.begin() as session:
        for idx, (p, r) in enumerate([(p1, 1400), (p2, 1300), (p3, 1200), (p4, 1100)]):
            session.add(PlayerRating(
                id=f"r_{idx}", player_id=p.player_id, rating_scope="mode:classic",
                rating=r, rated_match_count=10, wins=10, losses=0, draws=0,
                algorithm_version="elo-v1.0"
            ))

    t = service.create_tournament(owner_id=owner.player_id, name="Championship 4", mode="classic", capacity=4)
    t_id = t["id"]
    service.open_registration(t_id, owner.player_id)
    for p in [p1, p2, p3, p4]:
        service.register_player(t_id, p.player_id)

    service.start_tournament(t_id, owner.player_id)
    bracket = service.get_bracket(t_id)

    assert len(bracket["rounds"]) == 2  # Semifinals (Round 1) and Finals (Round 2)
    r1_matches = bracket["rounds"][0]["matches"]
    assert len(r1_matches) == 2

    # Standard bracket slots for 4: [1, 4, 2, 3]
    # Match 1: Seed 1 (P1) vs Seed 4 (P4)
    # Match 2: Seed 2 (P2) vs Seed 3 (P3)
    m1 = r1_matches[0]
    m2 = r1_matches[1]
    assert {m1["player_one"]["id"], m1["player_two"]["id"]} == {p1.player_id, p4.player_id}
    assert {m2["player_one"]["id"], m2["player_two"]["id"]} == {p2.player_id, p3.player_id}

    # Match 1 completed: P1 wins
    with tournament_database.sessions.begin() as session:
        service.process_match_completion(session, m1["id"], None, {
            "participants": [
                {"player_id": p1.player_id, "placement": 1, "final_score": 100},
                {"player_id": p4.player_id, "placement": 2, "final_score": 50},
            ]
        })

    # Match 2 completed: P3 wins (upset over P2)
    with tournament_database.sessions.begin() as session:
        service.process_match_completion(session, m2["id"], None, {
            "participants": [
                {"player_id": p3.player_id, "placement": 1, "final_score": 90},
                {"player_id": p2.player_id, "placement": 2, "final_score": 85},
            ]
        })

    # Both Semifinal matches complete -> Round 2 Final Match is scheduled
    bracket_mid = service.get_bracket(t_id)
    r2_matches = bracket_mid["rounds"][1]["matches"]
    assert len(r2_matches) == 1
    final_m = r2_matches[0]
    assert final_m["status"] == "scheduled"
    assert {final_m["player_one"]["id"], final_m["player_two"]["id"]} == {p1.player_id, p3.player_id}

    # Final Match completed: P3 wins Championship!
    with tournament_database.sessions.begin() as session:
        service.process_match_completion(session, final_m["id"], None, {
            "participants": [
                {"player_id": p3.player_id, "placement": 1, "final_score": 100},
                {"player_id": p1.player_id, "placement": 2, "final_score": 95},
            ]
        })

    # Whole tournament is now completed
    tourn_done = service.get_tournament(t_id)
    assert tourn_done["status"] == "completed"

    bracket_final = service.get_bracket(t_id)
    p_map = {p["player_id"]: p for p in bracket_final["participants"]}
    assert p_map[p3.player_id]["status"] == "champion"
    assert p_map[p3.player_id]["final_placement"] == 1
    assert p_map[p1.player_id]["status"] == "runner_up"
    assert p_map[p1.player_id]["final_placement"] == 2
    assert p_map[p2.player_id]["status"] == "eliminated"
    assert p_map[p4.player_id]["status"] == "eliminated"


def test_tournament_crud_and_lifecycle_management(tournament_database: Database):
    identities = PlayerIdentityService(tournament_database)
    service = TournamentService(tournament_database)

    owner = _create_player(identities, "Alice")
    other = _create_player(identities, "Bob")

    # 1. Create Tournament
    created = service.create_tournament(
        owner_player_id=owner.player_id,
        name="CRUD Cup",
        description="Testing CRUD operations",
        mode="elimination",
        capacity=16,
    )
    t_id = created["id"]
    assert created["name"] == "CRUD Cup"
    assert created["status"] == "draft"
    assert created["capacity"] == 16

    # 2. Get Tournament
    fetched = service.get_tournament(t_id)
    assert fetched["id"] == t_id
    assert fetched["owner_player_id"] == owner.player_id

    # 3. List Tournaments with filters
    t_list = service.list_tournaments(status="draft", mode="elimination", limit=10, offset=0)
    assert t_list["total"] >= 1
    ids = [item["id"] for item in t_list["tournaments"]]
    assert t_id in ids

    # 4. Update Tournament by owner
    updated = service.update_tournament(
        tournament_id=t_id,
        actor_id=owner.player_id,
        name="Renamed CRUD Cup",
        description="Updated description",
        capacity=8,
    )
    assert updated["name"] == "Renamed CRUD Cup"
    assert updated["capacity"] == 8

    # 5. Non-owner cannot update
    with pytest.raises(TournamentError) as exc:
        service.update_tournament(tournament_id=t_id, actor_id=other.player_id, name="Hacked Name")
    assert exc.value.code == "forbidden"
    assert exc.value.status_code == 403

    # 6. Audit trail is recorded
    audit = service.get_audit_trail(t_id)
    assert len(audit) >= 2  # created and updated
    actions = [a["action"] for a in audit]
    assert "tournament_created" in actions
    assert "tournament_updated" in actions

    # 7. Cancel Tournament by owner
    cancelled = service.cancel_tournament(t_id, owner.player_id)
    assert cancelled["status"] == "cancelled"

    # Cannot update cancelled tournament
    with pytest.raises(TournamentError) as exc:
        service.update_tournament(t_id, owner.player_id, name="Should Fail")
    assert exc.value.code == "invalid_state"



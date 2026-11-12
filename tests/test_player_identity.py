from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select

from quizverse.persistence.db import Database
from quizverse.persistence.models import PlayerCredential, PlayerProfile, Quiz
from quizverse.persistence.player_identity import (
    LiveIdentityTickets,
    MatchHistoryService,
    MatchResultError,
    PlayerIdentityError,
    PlayerIdentityService,
    normalize_display_name,
)


@pytest.fixture
def migrated_database(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{(tmp_path / 'players.sqlite3').as_posix()}"
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(Config(str(project_root / "alembic.ini")), "head")
    finally:
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url
    database = Database(database_url)
    try:
        yield database
    finally:
        database.close()


def _result(profile_id: str, *, mode: str = "classic", outcome: str = "win") -> dict[str, object]:
    now = datetime.now(UTC)
    participants = [{
        "seat_index": 0, "player_id": profile_id, "team_id": None, "final_score": 1000,
        "correct_count": 1, "answer_count": 1, "placement": 1, "outcome": outcome,
        "disconnect_count": 0, "reconnect_expired": False, "forfeited": False,
    }]
    if mode == "classic":
        participants.append({
            "seat_index": 1, "player_id": None, "team_id": None, "final_score": 500,
            "correct_count": 0, "answer_count": 1, "placement": 2, "outcome": "loss",
            "disconnect_count": 1, "reconnect_expired": False, "forfeited": False,
        })
    elif mode == "team_battle":
        participants[0].update(team_id="violet", placement=None, outcome="team_win")
        for index, team_id, score, placement, result_outcome in (
            (1, "violet", 800, None, "team_win"),
            (2, "cyan", 500, None, "team_loss"),
            (3, "cyan", 400, None, "team_loss"),
        ):
            participants.append({
                "seat_index": index, "player_id": None, "team_id": team_id, "final_score": score,
                "correct_count": 1, "answer_count": 1, "placement": placement, "outcome": result_outcome,
                "disconnect_count": 0, "reconnect_expired": False, "forfeited": False,
            })
    elif mode == "escape_room":
        participants[0].update(final_score=None, correct_count=None, answer_count=None, placement=None)
        participants[0]["outcome"] = "cooperative_success"
        participants.append({
            "seat_index": 1, "player_id": None, "team_id": None, "final_score": None,
            "correct_count": None, "answer_count": None, "placement": None, "outcome": "cooperative_success",
            "disconnect_count": 0, "reconnect_expired": False, "forfeited": False,
        })
    kind = "team" if mode == "team_battle" else "cooperative" if mode == "escape_room" else "practice" if mode == "solo_practice" else "individual"
    return {
        "source": "game_engine", "engine_match_id": hashlib.sha256(f"{mode}-{now.isoformat()}".encode()).hexdigest()[:32],
        "mode": mode, "status": "completed", "result_kind": kind, "rules_version": "stage-7-v1",
        "started_at": now - timedelta(minutes=1), "completed_at": now,
        "question_set_hash": hashlib.sha256(b"questions").hexdigest(), "participants": participants,
        "teams": [], "result_context": {},
    }


def test_player_credentials_are_hashed_rotated_revoked_and_represented_anonymously(migrated_database: Database):
    service = PlayerIdentityService(migrated_database)
    issued = service.create("  Café  ")
    assert issued.display_name == "Café"
    assert len(issued.token) >= 40
    assert "token=<redacted>" in repr(issued)
    with migrated_database.session() as session:
        credential = session.scalar(select(PlayerCredential).where(PlayerCredential.player_id == issued.player_id))
        assert credential is not None and credential.token_hash != issued.token
        assert credential.token_hash == hashlib.sha256(issued.token.encode()).hexdigest()
    assert service.authenticate(issued.token)["id"] == issued.player_id
    rotated = service.rotate(issued.player_id)
    with pytest.raises(PlayerIdentityError):
        service.authenticate(issued.token)
    assert service.authenticate(rotated.token)["id"] == issued.player_id
    service.anonymize(issued.player_id)
    with pytest.raises(PlayerIdentityError):
        service.authenticate(rotated.token)
    profile = service.profile(issued.player_id)
    assert profile is not None and profile["status"] == "anonymized"
    assert profile["display_name"] == "Deleted player"
    with migrated_database.session() as session:
        credential = session.scalar(select(PlayerCredential).where(PlayerCredential.player_id == issued.player_id))
        assert credential is not None and credential.revoked_at is not None


def test_display_names_are_not_unique_identity_keys_and_are_strictly_normalized(migrated_database: Database):
    service = PlayerIdentityService(migrated_database)
    first = service.create("Same name")
    second = service.create("Same name")
    assert first.player_id != second.player_id
    assert normalize_display_name("  N\u0303  ") == "Ñ"
    for name in ("", "   ", "\nName", "!!!", "x" * 25):
        with pytest.raises((ValueError, PlayerIdentityError)):
            service.create(name)
    with pytest.raises(PlayerIdentityError):
        service.update_name(first.player_id, "Updated", expected_version=9)
    changed = service.update_name(first.player_id, "Updated", expected_version=1)
    assert changed["display_name"] == "Updated" and changed["version"] == 2


def test_single_use_live_identity_tickets_expire_and_are_not_credentials():
    now = [10.0]
    tickets = LiveIdentityTickets(ttl_seconds=5, clock=lambda: now[0])
    ticket = tickets.issue("a" * 32)
    assert ticket and tickets.consume(ticket) == "a" * 32
    assert tickets.consume(ticket) is None
    expired = tickets.issue("b" * 32)
    now[0] += 6
    assert tickets.consume(expired) is None


def test_match_history_is_idempotent_private_and_keeps_mode_semantics(migrated_database: Database):
    identities = PlayerIdentityService(migrated_database)
    player = identities.create("History player")
    history = MatchHistoryService(migrated_database)
    result = _result(player.player_id)
    assert history.persist_completed(result) is True
    assert history.persist_completed(result) is False
    conflicting = {**result, "participants": [dict(result["participants"][0], final_score=2000), result["participants"][1]]}
    with pytest.raises(MatchResultError, match="conflicting"):
        history.persist_completed(conflicting)
    page = history.list_matches(player.player_id, limit=10, offset=0)
    assert page["history_available"] and page["total"] == 1
    match = page["matches"][0]
    assert match["final_score"] == 1000 and match["outcome"] == "win"
    assert "player_id" not in match and "result_digest" not in match and "question_set_hash" not in match
    stats = history.stats(player.player_id)
    assert (stats["completed_matches"], stats["wins"], stats["losses"], stats["draws"]) == (1, 1, 0, 0)


@pytest.mark.parametrize(
    ("mode", "kind", "outcome", "wins"),
    [
        ("team_battle", "team", "team_win", 1),
        ("escape_room", "cooperative", "cooperative_success", 0),
        ("solo_practice", "practice", "practice", 0),
    ],
)
def test_team_cooperative_and_practice_results_are_not_misclassified(
    migrated_database: Database, mode: str, kind: str, outcome: str, wins: int,
):
    identity = PlayerIdentityService(migrated_database).create(f"{mode} user")
    history = MatchHistoryService(migrated_database)
    result = _result(identity.player_id, mode=mode, outcome=outcome)
    if mode == "team_battle":
        result["teams"] = [
            {"team_id": "violet", "score": 1800, "correct_count": 2, "response_time_ms": 700, "placement": 1, "outcome": "win"},
            {"team_id": "cyan", "score": 900, "correct_count": 2, "response_time_ms": 800, "placement": 2, "outcome": "loss"},
        ]
    if mode == "escape_room":
        result["result_context"] = {"outcome": "victory", "solved_puzzles": 10}
    if mode == "solo_practice":
        result["participants"][0]["placement"] = None
        result["result_context"] = {"correct_answers": 1}
    assert history.persist_completed(result)
    saved = history.list_matches(identity.player_id, limit=10, offset=0)["matches"][0]
    assert saved["result_kind"] == kind and saved["outcome"] == outcome
    assert history.stats(identity.player_id)["wins"] == wins


def test_untrusted_invalid_or_duplicate_participant_results_roll_back(migrated_database: Database):
    identity = PlayerIdentityService(migrated_database).create("Rollback user")
    history = MatchHistoryService(migrated_database)
    forged = _result(identity.player_id)
    forged["source"] = "browser"
    with pytest.raises(MatchResultError):
        history.persist_completed(forged)
    duplicated = _result(identity.player_id)
    duplicated["participants"].append(dict(duplicated["participants"][0], seat_index=1))
    with pytest.raises(MatchResultError, match="unique"):
        history.persist_completed(duplicated)
    invalid_fk = _result(identity.player_id)
    invalid_fk["participants"][0]["player_id"] = "f" * 32
    with pytest.raises(Exception):
        history.persist_completed(invalid_fk)
    assert history.list_matches(identity.player_id, limit=10, offset=0)["total"] == 0


def test_profile_without_history_is_distinguished_from_zero_win_history(migrated_database: Database):
    profile = PlayerIdentityService(migrated_database).create("New player")
    stats = MatchHistoryService(migrated_database).stats(profile.player_id)
    assert stats["history_available"] is False
    assert stats["completed_matches"] == 0 and stats["wins"] == 0


def test_additive_migration_downgrade_upgrade_preserves_existing_quiz_rows(migrated_database: Database):
    project_root = Path(__file__).resolve().parents[1]
    database_url = migrated_database.engine.url.render_as_string(hide_password=False)
    now = datetime.now(UTC)
    with migrated_database.sessions.begin() as session:
        session.add(Quiz(
            id="legacy-quiz", owner_id=None, title="Existing quiz", description=None, category="science",
            difficulty="easy", visibility="public", status="published", default_time_limit_seconds=15,
            provenance="built_in_reviewed", version=1, created_at=now, updated_at=now,
        ))
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        config = Config(str(project_root / "alembic.ini"))
        command.downgrade(config, "4e8fc5a1c220")
        assert "player_profiles" not in inspect(migrated_database.engine).get_table_names()
        with migrated_database.session() as session:
            assert session.get(Quiz, "legacy-quiz") is not None
        command.upgrade(config, "head")
        tables = set(inspect(migrated_database.engine).get_table_names())
        assert {"player_profiles", "player_credentials", "competitive_matches", "match_participants", "match_teams"} <= tables
        with migrated_database.session() as session:
            assert session.get(Quiz, "legacy-quiz") is not None
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous

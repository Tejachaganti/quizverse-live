"""Automated tests for Creator Profiles in QuizVerse Live Community."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from quizverse.community.creator_service import CreatorService
from quizverse.persistence.db import Database
from quizverse.persistence.models import PlayerBlock
from quizverse.persistence.player_identity import PlayerIdentityService


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'creator-profiles-test.sqlite3').as_posix()}"
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "quizverse" / "migrations"))
    try:
        command.upgrade(config, "head")
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
def identity_service(test_db: Database) -> PlayerIdentityService:
    return PlayerIdentityService(test_db)


@pytest.fixture
def creator_service(test_db: Database) -> CreatorService:
    return CreatorService(test_db)


def test_get_or_create_creator_profile(creator_service: CreatorService, identity_service: PlayerIdentityService):
    claim = identity_service.create("AliceCreator")
    profile = creator_service.get_or_create_creator_profile(claim.player_id)
    assert profile is not None
    assert profile["creator_id"] == claim.player_id
    assert profile["display_name"] == "AliceCreator"
    assert profile["follower_count"] == 0
    assert profile["published_quiz_count"] == 0


def test_update_creator_profile(creator_service: CreatorService, identity_service: PlayerIdentityService):
    claim = identity_service.create("BobWriter")
    updated = creator_service.update_creator_profile(
        player_id=claim.player_id,
        bio="Avid science and trivia geek.",
        specializations=["Science"],
    )
    assert updated["bio"] == "Avid science and trivia geek."
    assert "Science" in updated["specializations"]

    retrieved = creator_service.get_creator_profile(claim.player_id)
    assert retrieved is not None
    assert retrieved["bio"] == "Avid science and trivia geek."
    assert "Science" in retrieved["specializations"]


def test_list_creators_with_filtering(creator_service: CreatorService, identity_service: PlayerIdentityService):
    c1 = identity_service.create("HistoryBuff")
    creator_service.update_creator_profile(c1.player_id, specializations=["History"])

    c2 = identity_service.create("MathWhiz")
    creator_service.update_creator_profile(c2.player_id, specializations=["Mathematics"])

    history_creators = creator_service.list_creators(specialization="History")
    assert any(c["creator_id"] == c1.player_id for c in history_creators)
    assert not any(c["creator_id"] == c2.player_id for c in history_creators)

    all_creators = creator_service.list_creators()
    assert len(all_creators) >= 2


def test_creator_profile_blocking_isolation(creator_service: CreatorService, identity_service: PlayerIdentityService, test_db: Database):
    c1 = identity_service.create("CreatorOne")
    c2 = identity_service.create("ViewerBlocked")

    with test_db.sessions.begin() as session:
        session.add(PlayerBlock(id=secrets.token_hex(16), blocker_id=c1.player_id, blocked_id=c2.player_id))

    # When blocked player tries to view creator profile, PermissionError is raised
    with pytest.raises(PermissionError, match="blocking"):
        creator_service.get_creator_profile(c1.player_id, viewer_id=c2.player_id)

"""Automated tests for Creator Follows, Counts, and Notification Integration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from quizverse.community.creator_service import CreatorService
from quizverse.persistence.db import Database
from quizverse.persistence.player_identity import PlayerIdentityService


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'creator-follow-test.sqlite3').as_posix()}"
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


def test_follow_and_unfollow_creator(creator_service: CreatorService, identity_service: PlayerIdentityService):
    creator = identity_service.create("PopularAuthor")
    fan = identity_service.create("DevotedFan")

    # Initial state
    assert creator_service.is_following(fan.player_id, creator.player_id) is False

    # Follow
    res = creator_service.follow_creator(fan.player_id, creator.player_id)
    assert res["status"] in {"followed", "already_following"}
    assert creator_service.is_following(fan.player_id, creator.player_id) is True

    # Creator follower count updated
    profile = creator_service.get_creator_profile(creator.player_id)
    assert profile["follower_count"] == 1

    # Unfollow
    unres = creator_service.unfollow_creator(fan.player_id, creator.player_id)
    assert unres["status"] == "unfollowed"
    assert creator_service.is_following(fan.player_id, creator.player_id) is False

    # Creator follower count decremented
    profile_after = creator_service.get_creator_profile(creator.player_id)
    assert profile_after["follower_count"] == 0


def test_self_follow_rejected(creator_service: CreatorService, identity_service: PlayerIdentityService):
    creator = identity_service.create("Narcissist")
    with pytest.raises(ValueError, match="cannot follow yourself"):
        creator_service.follow_creator(creator.player_id, creator.player_id)


def test_duplicate_follow_idempotent(creator_service: CreatorService, identity_service: PlayerIdentityService):
    creator = identity_service.create("StarCreator")
    fan = identity_service.create("DoubleClicker")

    creator_service.follow_creator(fan.player_id, creator.player_id)
    # Second follow should be idempotent
    res = creator_service.follow_creator(fan.player_id, creator.player_id)
    assert res["status"] == "already_following"

    profile = creator_service.get_creator_profile(creator.player_id)
    assert profile["follower_count"] == 1


def test_list_following_and_followers(creator_service: CreatorService, identity_service: PlayerIdentityService):
    c1 = identity_service.create("ArtistA")
    c2 = identity_service.create("ArtistB")
    fan = identity_service.create("SuperFan")

    creator_service.follow_creator(fan.player_id, c1.player_id)
    creator_service.follow_creator(fan.player_id, c2.player_id)

    following = creator_service.list_following(fan.player_id)
    following_ids = [f["creator_id"] for f in following]
    assert c1.player_id in following_ids
    assert c2.player_id in following_ids

"""Automated tests for Quiz Bookmarks and Private Favorites."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from quizverse.community.favorite_service import FavoriteService
from quizverse.persistence.db import Database
from quizverse.persistence.models import Quiz, QuizQuestion
from quizverse.persistence.player_identity import PlayerIdentityService


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'fav-test.sqlite3').as_posix()}"
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
def favorite_service(test_db: Database) -> FavoriteService:
    return FavoriteService(test_db)


def create_quiz(db: Database, title: str = "World History") -> str:
    quiz_id = secrets.token_hex(8)
    now = datetime.now(UTC)
    with db.sessions.begin() as session:
        session.add(
            Quiz(
                id=quiz_id,
                owner_id=None,
                title=title,
                description=f"Desc for {title}",
                category="History",
                difficulty="medium",
                visibility="public",
                status="published",
                default_time_limit_seconds=15,
                provenance="built_in_reviewed",
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        for i in range(2):
            session.add(
                QuizQuestion(
                    id=secrets.token_hex(8),
                    quiz_id=quiz_id,
                    prompt=f"Prompt {i+1}?",
                    option_a=f"A {i}",
                    option_b=f"B {i}",
                    option_c=f"C {i}",
                    option_d=f"D {i}",
                    correct_option_index=0,
                    question_format="multiple_choice",
                    position=i,
                    created_at=now,
                    updated_at=now,
                )
            )
    return quiz_id


def test_favorite_and_unfavorite(favorite_service: FavoriteService, identity_service: PlayerIdentityService, test_db: Database):
    player = identity_service.create("Bookmarker")
    quiz_id = create_quiz(test_db, "Renaissance Art")

    assert favorite_service.is_favorite(player.player_id, quiz_id) is False

    add_res = favorite_service.add_favorite(player.player_id, quiz_id)
    assert add_res["status"] == "favorited"
    assert favorite_service.is_favorite(player.player_id, quiz_id) is True

    # Duplicate add is idempotent
    add_again = favorite_service.add_favorite(player.player_id, quiz_id)
    assert add_again["status"] == "already_favorited"

    # List favorites
    favs = favorite_service.list_favorites(player.player_id)
    assert len(favs) == 1
    assert favs[0]["quiz_id"] == quiz_id

    # Remove favorite
    rem_res = favorite_service.remove_favorite(player.player_id, quiz_id)
    assert rem_res["status"] == "unfavorited"
    assert favorite_service.is_favorite(player.player_id, quiz_id) is False
    assert len(favorite_service.list_favorites(player.player_id)) == 0


def test_private_favorites_isolation_between_players(
    favorite_service: FavoriteService,
    identity_service: PlayerIdentityService,
    test_db: Database,
):
    p1 = identity_service.create("PlayerAlpha")
    p2 = identity_service.create("PlayerBeta")
    q1 = create_quiz(test_db, "Alpha Quiz")
    q2 = create_quiz(test_db, "Beta Quiz")

    favorite_service.add_favorite(p1.player_id, q1)
    favorite_service.add_favorite(p2.player_id, q2)

    p1_favs = favorite_service.list_favorites(p1.player_id)
    p2_favs = favorite_service.list_favorites(p2.player_id)

    assert [f["quiz_id"] for f in p1_favs] == [q1]
    assert [f["quiz_id"] for f in p2_favs] == [q2]

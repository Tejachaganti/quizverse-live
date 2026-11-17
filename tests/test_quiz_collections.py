"""Automated tests for Quiz Collections, Playlists, Ordering, and Visibility."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from quizverse.community.collection_service import CollectionService
from quizverse.persistence.db import Database
from quizverse.persistence.models import Quiz, QuizQuestion
from quizverse.persistence.player_identity import PlayerIdentityService


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'col-test.sqlite3').as_posix()}"
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
def collection_service(test_db: Database) -> CollectionService:
    return CollectionService(test_db)


def create_quiz(db: Database, title: str) -> str:
    quiz_id = secrets.token_hex(8)
    now = datetime.now(UTC)
    with db.sessions.begin() as session:
        session.add(
            Quiz(
                id=quiz_id,
                owner_id=None,
                title=title,
                description=f"Desc for {title}",
                category="General",
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


def test_create_and_manage_collection(collection_service: CollectionService, identity_service: PlayerIdentityService, test_db: Database):
    owner = identity_service.create("Curator")
    q1 = create_quiz(test_db, "Volcanoes 101")
    q2 = create_quiz(test_db, "Earthquakes 102")

    col = collection_service.create_collection(
        owner_id=owner.player_id,
        title="Geology Masterclass",
        description="Comprehensive geological sciences playlist",
        visibility="public",
    )
    assert col["title"] == "Geology Masterclass"
    assert col["owner_id"] == owner.player_id
    assert col["item_count"] == 0

    # Add items
    collection_service.add_quiz_to_collection(owner.player_id, col["id"], q1)
    col_updated = collection_service.add_quiz_to_collection(owner.player_id, col["id"], q2)
    assert col_updated["item_count"] == 2
    assert len(col_updated["items"]) == 2
    assert col_updated["items"][0]["quiz_id"] == q1
    assert col_updated["items"][1]["quiz_id"] == q2

    # Remove item
    col_after_remove = collection_service.remove_quiz_from_collection(owner.player_id, col["id"], q1)
    assert col_after_remove["item_count"] == 1
    assert col_after_remove["items"][0]["quiz_id"] == q2


def test_collection_authorization_and_private_visibility(
    collection_service: CollectionService,
    identity_service: PlayerIdentityService,
):
    owner = identity_service.create("PlaylistOwner")
    stranger = identity_service.create("CuriousStranger")

    priv_col = collection_service.create_collection(
        owner_id=owner.player_id,
        title="Personal Study Plan",
        visibility="private",
    )

    # Owner can view private collection
    assert collection_service.get_collection(priv_col["id"], viewer_id=owner.player_id) is not None

    # Stranger cannot view private collection
    with pytest.raises(PermissionError):
        collection_service.get_collection(priv_col["id"], viewer_id=stranger.player_id)

    # Stranger cannot edit owner's collection
    with pytest.raises(PermissionError):
        collection_service.update_collection(owner_id=stranger.player_id, collection_id=priv_col["id"], title="Hijacked")

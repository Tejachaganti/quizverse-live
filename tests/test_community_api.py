"""Comprehensive REST API integration tests for Community & Creator Ecosystem."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from quizverse.persistence.db import Database
from quizverse.persistence.models import PlayerRole, Quiz, QuizQuestion
from quizverse.persistence.player_identity import PlayerIdentityService
from quizverse.realtime import create_app


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'comm-api-test.sqlite3').as_posix()}"
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
def app(test_db: Database):
    return create_app(database=test_db)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def identity_service(test_db: Database) -> PlayerIdentityService:
    return PlayerIdentityService(test_db)


@pytest.fixture
def creator_player(identity_service: PlayerIdentityService):
    claim = identity_service.create("CreatorSam")
    return {"id": claim.player_id, "token": claim.token}


@pytest.fixture
def player_gamer(identity_service: PlayerIdentityService):
    claim = identity_service.create("GamerJoe")
    return {"id": claim.player_id, "token": claim.token}


@pytest.fixture
def moderator_player(identity_service: PlayerIdentityService, test_db: Database):
    claim = identity_service.create("ModAdmin")
    with test_db.sessions.begin() as session:
        session.add(
            PlayerRole(
                id=secrets.token_hex(16),
                player_id=claim.player_id,
                role="moderator",
                granted_by="test",
                created_at=datetime.now(UTC),
            )
        )
    return {"id": claim.player_id, "token": claim.token}


def create_quiz(db: Database, title: str = "Test Community Quiz") -> str:
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
                status="draft",
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
                    prompt=f"Question {i+1}?",
                    option_a=f"Option 1 ({i})",
                    option_b=f"Option 2 ({i})",
                    option_c=f"Option 3 ({i})",
                    option_d=f"Option 4 ({i})",
                    correct_option_index=0,
                    question_format="multiple_choice",
                    position=i,
                    created_at=now,
                    updated_at=now,
                )
            )
    return quiz_id


def test_api_overview(client: TestClient):
    resp = client.get("/api/community/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert "featured" in data
    assert "trending" in data
    assert "popular" in data
    assert "recent" in data
    assert "top_rated" in data


def test_api_publish_and_play_lifecycle(
    client: TestClient,
    test_db: Database,
    creator_player: dict,
    player_gamer: dict,
):
    qid = create_quiz(test_db, "Marine Biology Quiz")

    # Publish
    pub_resp = client.post(
        f"/api/community/quizzes/{qid}/publish",
        json={"visibility": "public", "tags": ["biology", "marine"]},
        headers={"Authorization": f"Bearer {creator_player['token']}"},
    )
    assert pub_resp.status_code == 200
    assert pub_resp.json()["status"] == "published"

    # Play
    play_resp = client.post(
        f"/api/community/quizzes/{qid}/play",
        json={"score": 100, "max_score": 100, "completed": True, "duration_seconds": 60},
        headers={"Authorization": f"Bearer {player_gamer['token']}"},
    )
    assert play_resp.status_code == 200
    assert play_resp.json()["status"] == "play_recorded"

    # Review
    rev_resp = client.post(
        f"/api/community/quizzes/{qid}/reviews",
        json={"rating": 5, "review_text": "Superb quiz!"},
        headers={"Authorization": f"Bearer {player_gamer['token']}"},
    )
    assert rev_resp.status_code == 200
    assert rev_resp.json()["rating"] == 5

    # Get reviews
    list_rev_resp = client.get(f"/api/community/quizzes/{qid}/reviews")
    assert list_rev_resp.status_code == 200
    assert len(list_rev_resp.json()["items"]) == 1


def test_api_creator_profile_and_follow(
    client: TestClient,
    creator_player: dict,
    player_gamer: dict,
):
    # Update creator profile
    update_resp = client.put(
        "/api/community/creators/me",
        json={"bio": "Lifelong trivia master", "specializations": ["Science"]},
        headers={"Authorization": f"Bearer {creator_player['token']}"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["bio"] == "Lifelong trivia master"

    # Follow creator
    follow_resp = client.post(
        f"/api/community/creators/follow/{creator_player['id']}",
        headers={"Authorization": f"Bearer {player_gamer['token']}"},
    )
    assert follow_resp.status_code == 200
    assert follow_resp.json()["status"] in {"followed", "already_following"}

    # Unfollow creator
    unfollow_resp = client.delete(
        f"/api/community/creators/follow/{creator_player['id']}",
        headers={"Authorization": f"Bearer {player_gamer['token']}"},
    )
    assert unfollow_resp.status_code == 200
    assert unfollow_resp.json()["status"] == "unfollowed"


def test_api_collections(
    client: TestClient,
    test_db: Database,
    creator_player: dict,
):
    qid = create_quiz(test_db, "Collection Quiz")

    # Create collection
    create_col = client.post(
        "/api/community/collections",
        json={"title": "Test Playlist", "description": "A curated set", "visibility": "public"},
        headers={"Authorization": f"Bearer {creator_player['token']}"},
    )
    assert create_col.status_code in {200, 201}
    col_id = create_col.json()["id"]

    # Add quiz to collection
    add_item = client.post(
        f"/api/community/collections/{col_id}/quizzes/{qid}",
        headers={"Authorization": f"Bearer {creator_player['token']}"},
    )
    assert add_item.status_code in {200, 201}
    assert add_item.json()["item_count"] == 1

    # Remove quiz
    del_item = client.delete(
        f"/api/community/collections/{col_id}/quizzes/{qid}",
        headers={"Authorization": f"Bearer {creator_player['token']}"},
    )
    assert del_item.status_code == 200
    assert del_item.json()["item_count"] == 0


def test_api_moderation_queue_and_permissions(
    client: TestClient,
    test_db: Database,
    creator_player: dict,
    player_gamer: dict,
    moderator_player: dict,
):
    qid = create_quiz(test_db, "Reported Quiz")

    # Ordinary user reports quiz
    rep_resp = client.post(
        "/api/community/reports",
        json={"target_type": "quiz", "target_id": qid, "reason": "inappropriate", "description": "Spam content"},
        headers={"Authorization": f"Bearer {player_gamer['token']}"},
    )
    assert rep_resp.status_code in {200, 201}
    rep_id = rep_resp.json()["report_id"]

    # Ordinary user cannot access moderation queue
    user_mod_resp = client.get(
        "/api/community/moderation/reports",
        headers={"Authorization": f"Bearer {player_gamer['token']}"},
    )
    assert user_mod_resp.status_code == 403

    # Moderator can access moderation queue
    mod_list_resp = client.get(
        "/api/community/moderation/reports",
        headers={"Authorization": f"Bearer {moderator_player['token']}"},
    )
    assert mod_list_resp.status_code == 200
    assert len(mod_list_resp.json()["items"]) >= 1

    # Moderator resolves report
    resolve_resp = client.post(
        f"/api/community/moderation/reports/{rep_id}/resolve",
        json={"action": "suspend_quiz", "notes": "Confirmed spam."},
        headers={"Authorization": f"Bearer {moderator_player['token']}"},
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["status"] == "resolved"

"""Automated tests for Creator Analytics, Play Metrics, and Performance Breakdown."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from quizverse.community.creator_service import CreatorService
from quizverse.community.publication_service import PublicationService
from quizverse.persistence.db import Database
from quizverse.persistence.models import Quiz, QuizQuestion
from quizverse.persistence.player_identity import PlayerIdentityService


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'creator-analytics-test.sqlite3').as_posix()}"
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
def publication_service(test_db: Database) -> PublicationService:
    return PublicationService(test_db)


@pytest.fixture
def creator_service(test_db: Database) -> CreatorService:
    return CreatorService(test_db)


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
                category="Science",
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
                    prompt=f"Q{i+1} for {title}?",
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


def test_creator_analytics_computation(
    test_db: Database,
    publication_service: PublicationService,
    creator_service: CreatorService,
    identity_service: PlayerIdentityService,
):
    creator = identity_service.create("AnalyticsCreator")
    p1 = identity_service.create("GamerOne")
    p2 = identity_service.create("GamerTwo")

    q1 = create_quiz(test_db, "Quiz A")
    q2 = create_quiz(test_db, "Quiz B")

    publication_service.publish_quiz(creator.player_id, q1)
    publication_service.publish_quiz(creator.player_id, q2)

    # Record plays
    publication_service.record_quiz_play(p1.player_id, q1, score=100)
    publication_service.record_quiz_play(p2.player_id, q1, score=80)
    publication_service.record_quiz_play(p1.player_id, q2, score=90)

    # Fetch analytics
    analytics = creator_service.get_creator_analytics(creator.player_id)
    assert analytics["creator_id"] == creator.player_id
    assert analytics["total_published_quizzes"] == 2
    assert analytics["total_plays"] == 3
    assert analytics["unique_players"] == 2
    assert len(analytics["popular_quizzes"]) == 2


def test_creator_analytics_time_windows(
    test_db: Database,
    publication_service: PublicationService,
    creator_service: CreatorService,
    identity_service: PlayerIdentityService,
):
    creator = identity_service.create("WindowCreator")
    p1 = identity_service.create("PlayerOne")
    q1 = create_quiz(test_db, "Quiz One")
    publication_service.publish_quiz(creator.player_id, q1)
    publication_service.record_quiz_play(p1.player_id, q1, score=100)

    res_all = creator_service.get_creator_analytics(creator.player_id, time_window="all_time")
    res_7d = creator_service.get_creator_analytics(creator.player_id, time_window="7_days")
    res_30d = creator_service.get_creator_analytics(creator.player_id, time_window="30_days")

    assert res_all["total_plays"] == 1
    assert res_7d["total_plays"] == 1
    assert res_30d["total_plays"] == 1

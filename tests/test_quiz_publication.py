"""Automated tests for Quiz Publication lifecycle, validation, and play logging."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from quizverse.community.publication_service import PublicationService
from quizverse.persistence.db import Database
from quizverse.persistence.models import Quiz, QuizQuestion
from quizverse.persistence.player_identity import PlayerIdentityService


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'quiz-pub-test.sqlite3').as_posix()}"
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


def create_test_quiz(
    db: Database,
    title: str = "Cosmology Fundamentals",
    category: str = "Science",
    difficulty: str = "medium",
    questions_count: int = 2,
) -> str:
    quiz_id = secrets.token_hex(8)
    now = datetime.now(UTC)
    with db.sessions.begin() as session:
        quiz = Quiz(
            id=quiz_id,
            owner_id=None,
            title=title,
            description=f"A great quiz on {title}",
            category=category,
            difficulty=difficulty,
            visibility="public",
            status="draft",
            default_time_limit_seconds=15,
            provenance="built_in_reviewed",
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(quiz)
        for i in range(questions_count):
            q = QuizQuestion(
                id=secrets.token_hex(8),
                quiz_id=quiz_id,
                prompt=f"Cosmology Question {i+1}: What is the age of the Universe?",
                option_a=f"13.8 billion years ({i})",
                option_b=f"4.5 billion years ({i})",
                option_c=f"1.0 billion years ({i})",
                option_d=f"100 billion years ({i})",
                correct_option_index=0,
                question_format="multiple_choice",
                explanation="Current astronomical measurements place cosmic age at 13.8 Ga.",
                position=i,
                created_at=now,
                updated_at=now,
            )
            session.add(q)
    return quiz_id


def test_publish_valid_quiz(publication_service: PublicationService, identity_service: PlayerIdentityService, test_db: Database):
    creator = identity_service.create("ProfAstro")
    quiz_id = create_test_quiz(test_db, title="Astrophysics 101", questions_count=3)

    result = publication_service.publish_quiz(
        creator_id=creator.player_id,
        quiz_id=quiz_id,
        visibility="public",
    )
    assert result["status"] == "published"
    assert result["visibility"] == "public"
    assert result["creator_id"] == creator.player_id
    assert result["validation_summary"]["question_count"] == 3
    assert result["validation_summary"]["average_quality_score"] > 0


def test_publish_quiz_too_few_questions_rejected(publication_service: PublicationService, identity_service: PlayerIdentityService, test_db: Database):
    creator = identity_service.create("RushedCreator")
    quiz_id = create_test_quiz(test_db, title="Too Short", questions_count=1)

    with pytest.raises(ValueError, match="at least 2 questions"):
        publication_service.publish_quiz(
            creator_id=creator.player_id,
            quiz_id=quiz_id,
            visibility="public",
        )


def test_publish_unauthorized_creator_rejected(publication_service: PublicationService, identity_service: PlayerIdentityService, test_db: Database):
    creator = identity_service.create("RealOwner")
    stranger = identity_service.create("Imposter")
    quiz_id = create_test_quiz(test_db, title="Original Quiz", questions_count=2)

    # First creator publishes
    publication_service.publish_quiz(creator.player_id, quiz_id, visibility="public")

    # Imposter tries to publish the same quiz
    with pytest.raises(PermissionError, match="not authorized"):
        publication_service.publish_quiz(stranger.player_id, quiz_id, visibility="public")


def test_unpublish_and_republish_cycle(publication_service: PublicationService, identity_service: PlayerIdentityService, test_db: Database):
    creator = identity_service.create("CycleCreator")
    quiz_id = create_test_quiz(test_db, title="Lifecycle Test", questions_count=2)

    pub = publication_service.publish_quiz(creator.player_id, quiz_id, visibility="public")
    assert pub["status"] == "published"

    unpub = publication_service.unpublish_quiz(creator.player_id, quiz_id)
    assert unpub["status"] == "draft"

    repub = publication_service.publish_quiz(creator.player_id, quiz_id, visibility="unlisted")
    assert repub["status"] == "published"
    assert repub["visibility"] == "unlisted"


def test_record_quiz_play(publication_service: PublicationService, identity_service: PlayerIdentityService, test_db: Database):
    creator = identity_service.create("AuthorQuiz")
    player = identity_service.create("GamerPlayer")
    quiz_id = create_test_quiz(test_db, title="Play Test Quiz", questions_count=2)
    publication_service.publish_quiz(creator.player_id, quiz_id)

    play_rec = publication_service.record_quiz_play(
        player_id=player.player_id,
        quiz_id=quiz_id,
        score=100,
        max_score=100,
        completed=True,
        duration_seconds=45,
    )
    assert play_rec["quiz_id"] == quiz_id
    assert play_rec["player_id"] == player.player_id
    assert play_rec["score"] == 100

    pub = publication_service.get_publication(quiz_id)
    assert pub["total_plays"] == 1

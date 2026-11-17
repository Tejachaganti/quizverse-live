"""Automated tests for Ratings, Reviews, Verification, and Creator Responses."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from quizverse.community.publication_service import PublicationService
from quizverse.community.review_service import ReviewService
from quizverse.persistence.db import Database
from quizverse.persistence.models import Quiz, QuizQuestion
from quizverse.persistence.player_identity import PlayerIdentityService


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'comm-rev-test.sqlite3').as_posix()}"
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
def review_service(test_db: Database) -> ReviewService:
    return ReviewService(test_db)


def setup_published_quiz(db: Database, pub_service: PublicationService, creator_id: str) -> str:
    quiz_id = secrets.token_hex(8)
    now = datetime.now(UTC)
    with db.sessions.begin() as session:
        quiz = Quiz(
            id=quiz_id,
            owner_id=None,
            title="Cellular Biology",
            description="Deep dive into cellular structures.",
            category="Biology",
            difficulty="medium",
            visibility="public",
            status="draft",
            default_time_limit_seconds=15,
            provenance="built_in_reviewed",
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(quiz)
        for i in range(2):
            session.add(
                QuizQuestion(
                    id=secrets.token_hex(8),
                    quiz_id=quiz_id,
                    prompt=f"Biology Q{i+1}: What is the powerhouse of the cell?",
                    option_a=f"Mitochondria {i}",
                    option_b=f"Ribosome {i}",
                    option_c=f"Nucleus {i}",
                    option_d=f"Golgi body {i}",
                    correct_option_index=0,
                    question_format="multiple_choice",
                    explanation="Mitochondria produce ATP.",
                    position=i,
                    created_at=now,
                    updated_at=now,
                )
            )
    pub_service.publish_quiz(creator_id, quiz_id)
    return quiz_id


def test_review_requires_prior_play(
    test_db: Database,
    publication_service: PublicationService,
    review_service: ReviewService,
    identity_service: PlayerIdentityService,
):
    creator = identity_service.create("BioTeacher")
    player = identity_service.create("StudentOne")
    quiz_id = setup_published_quiz(test_db, publication_service, creator.player_id)

    # Without playing, rating must be rejected
    with pytest.raises(ValueError, match="must play this quiz"):
        review_service.submit_review(player.player_id, quiz_id, rating=5, review_text="Loved it!")

    # Record play
    publication_service.record_quiz_play(player.player_id, quiz_id, score=100)

    # Now rating succeeds
    rev = review_service.submit_review(player.player_id, quiz_id, rating=5, review_text="Loved it!")
    assert rev["rating"] == 5
    assert rev["review_text"] == "Loved it!"
    assert rev["player_id"] == player.player_id


def test_creator_cannot_review_own_quiz(
    test_db: Database,
    publication_service: PublicationService,
    review_service: ReviewService,
    identity_service: PlayerIdentityService,
):
    creator = identity_service.create("SelfRater")
    quiz_id = setup_published_quiz(test_db, publication_service, creator.player_id)
    publication_service.record_quiz_play(creator.player_id, quiz_id, score=100)

    with pytest.raises(ValueError, match="cannot rate their own quizzes"):
        review_service.submit_review(creator.player_id, quiz_id, rating=5)


def test_aggregate_rating_recalculations(
    test_db: Database,
    publication_service: PublicationService,
    review_service: ReviewService,
    identity_service: PlayerIdentityService,
):
    creator = identity_service.create("ProfSmith")
    p1 = identity_service.create("RaterA")
    p2 = identity_service.create("RaterB")
    quiz_id = setup_published_quiz(test_db, publication_service, creator.player_id)

    publication_service.record_quiz_play(p1.player_id, quiz_id, score=80)
    publication_service.record_quiz_play(p2.player_id, quiz_id, score=90)

    review_service.submit_review(p1.player_id, quiz_id, rating=4)
    review_service.submit_review(p2.player_id, quiz_id, rating=5)

    pub = publication_service.get_publication(quiz_id)
    assert pub["total_reviews"] == 2
    assert pub["average_rating"] == 4.5


def test_creator_response_and_helpful_votes(
    test_db: Database,
    publication_service: PublicationService,
    review_service: ReviewService,
    identity_service: PlayerIdentityService,
):
    creator = identity_service.create("TeacherBio")
    student = identity_service.create("ReviewerStudent")
    voter = identity_service.create("VoterPlayer")
    quiz_id = setup_published_quiz(test_db, publication_service, creator.player_id)

    publication_service.record_quiz_play(student.player_id, quiz_id, score=95)
    rev = review_service.submit_review(student.player_id, quiz_id, rating=5, review_text="Great explanations!")

    # Other player marks helpful
    helpful_res = review_service.mark_helpful(voter.player_id, rev["id"])
    assert helpful_res["helpful_count"] == 1

    # Creator responds
    resp_res = review_service.creator_respond(
        creator_id=creator.player_id,
        review_id=rev["id"],
        response_text="Thank you so much! Glad it helped.",
    )
    assert resp_res["creator_response"] == "Thank you so much! Glad it helped."
    assert resp_res["creator_responded_at"] is not None

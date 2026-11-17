"""Automated tests for Community Discovery, Feeds, and Multi-Facet Search."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from quizverse.community.discovery_service import DiscoveryService
from quizverse.community.publication_service import PublicationService
from quizverse.persistence.db import Database
from quizverse.persistence.models import Quiz, QuizQuestion
from quizverse.persistence.player_identity import PlayerIdentityService


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'comm-disc-test.sqlite3').as_posix()}"
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
def discovery_service(test_db: Database) -> DiscoveryService:
    return DiscoveryService(test_db)


def create_and_publish_quiz(
    db: Database,
    pub_service: PublicationService,
    creator_id: str,
    title: str,
    category: str = "Science",
    difficulty: str = "medium",
    visibility: str = "public",
) -> str:
    quiz_id = secrets.token_hex(8)
    now = datetime.now(UTC)
    with db.sessions.begin() as session:
        q = Quiz(
            id=quiz_id,
            owner_id=None,
            title=title,
            description=f"Description for {title}",
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
        session.add(q)
        for i in range(2):
            session.add(
                QuizQuestion(
                    id=secrets.token_hex(8),
                    quiz_id=quiz_id,
                    position=i,
                    prompt=f"Prompt {i+1} for {title}?",
                    option_a=f"Alpha {i}",
                    option_b=f"Beta {i}",
                    option_c=f"Gamma {i}",
                    option_d=f"Delta {i}",
                    correct_option_index=0,
                    question_format="multiple_choice",
                    explanation="Alpha is correct.",
                    created_at=now,
                    updated_at=now,
                )
            )
    pub_service.publish_quiz(creator_id, quiz_id, visibility=visibility)
    return quiz_id


def test_discovery_overview_trays(
    test_db: Database,
    publication_service: PublicationService,
    discovery_service: DiscoveryService,
    identity_service: PlayerIdentityService,
):
    creator = identity_service.create("FeedCreator")
    create_and_publish_quiz(test_db, publication_service, creator.player_id, "Quantum Mechanics", category="Physics")
    create_and_publish_quiz(test_db, publication_service, creator.player_id, "Ancient Rome", category="History")

    overview = discovery_service.get_discovery_overview()
    assert "featured" in overview
    assert "trending" in overview
    assert "popular" in overview
    assert "recent" in overview
    assert "top_rated" in overview
    assert len(overview["recent"]) >= 2


def test_discovery_search_and_faceted_filtering(
    test_db: Database,
    publication_service: PublicationService,
    discovery_service: DiscoveryService,
    identity_service: PlayerIdentityService,
):
    creator = identity_service.create("FilterCreator")
    q1 = create_and_publish_quiz(test_db, publication_service, creator.player_id, "Machine Learning Fundamentals", category="Technology", difficulty="hard")
    q2 = create_and_publish_quiz(test_db, publication_service, creator.player_id, "Organic Chemistry Basics", category="Chemistry", difficulty="easy")

    # Search by keyword
    res_kw = discovery_service.search_quizzes(query="Machine")
    assert res_kw["total"] == 1
    assert res_kw["items"][0]["quiz_id"] == q1

    # Filter by category
    res_cat = discovery_service.search_quizzes(category="Chemistry")
    assert res_cat["total"] == 1
    assert res_cat["items"][0]["quiz_id"] == q2

    # Filter by difficulty
    res_diff = discovery_service.search_quizzes(difficulty="hard")
    assert res_diff["total"] == 1
    assert res_diff["items"][0]["quiz_id"] == q1


def test_private_and_unlisted_isolation_from_public_discovery(
    test_db: Database,
    publication_service: PublicationService,
    discovery_service: DiscoveryService,
    identity_service: PlayerIdentityService,
):
    creator = identity_service.create("SecretCreator")
    pub_q = create_and_publish_quiz(test_db, publication_service, creator.player_id, "Public Biology Quiz", visibility="public")
    unlisted_q = create_and_publish_quiz(test_db, publication_service, creator.player_id, "Secret Unlisted Quiz", visibility="unlisted")
    priv_q = create_and_publish_quiz(test_db, publication_service, creator.player_id, "Hidden Private Quiz", visibility="private")

    res = discovery_service.search_quizzes()
    quiz_ids = [item["quiz_id"] for item in res["items"]]
    assert pub_q in quiz_ids
    assert unlisted_q not in quiz_ids
    assert priv_q not in quiz_ids

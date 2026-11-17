"""Automated tests for Community Moderation, Reports, Suspension, and Audit Trails."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from quizverse.community.moderation_service import ModerationService
from quizverse.community.publication_service import PublicationService
from quizverse.persistence.db import Database
from quizverse.persistence.models import Quiz, QuizQuestion
from quizverse.persistence.player_identity import PlayerIdentityService


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'mod-test.sqlite3').as_posix()}"
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
def moderation_service(test_db: Database) -> ModerationService:
    return ModerationService(test_db)


def setup_quiz(db: Database, pub_service: PublicationService, creator_id: str) -> str:
    quiz_id = secrets.token_hex(8)
    now = datetime.now(UTC)
    with db.sessions.begin() as session:
        session.add(
            Quiz(
                id=quiz_id,
                owner_id=None,
                title="Offensive Quiz Title",
                description="Needs review",
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
                    option_a=f"Yes {i}",
                    option_b=f"No {i}",
                    option_c=f"Maybe {i}",
                    option_d=f"Never {i}",
                    correct_option_index=0,
                    question_format="multiple_choice",
                    position=i,
                    created_at=now,
                    updated_at=now,
                )
            )
    pub_service.publish_quiz(creator_id, quiz_id)
    return quiz_id


def test_submit_report_and_deduplication(
    moderation_service: ModerationService,
    identity_service: PlayerIdentityService,
):
    reporter = identity_service.create("ReporterOne")
    target_quiz_id = secrets.token_hex(8)

    # First report
    rep = moderation_service.submit_report(
        reporter_id=reporter.player_id,
        target_type="quiz",
        target_id=target_quiz_id,
        reason="inappropriate",
        description="Contains offensive content.",
    )
    assert rep["status"] == "submitted"
    assert rep["report_id"] is not None

    # Duplicate report by same user
    rep_dup = moderation_service.submit_report(
        reporter_id=reporter.player_id,
        target_type="quiz",
        target_id=target_quiz_id,
        reason="spam",
    )
    assert rep_dup["status"] == "already_reported"
    assert rep_dup["report_id"] == rep["report_id"]


def test_moderator_permission_checks(
    moderation_service: ModerationService,
    identity_service: PlayerIdentityService,
):
    normal_user = identity_service.create("NormalUser")
    mod_user = identity_service.create("SiteModerator")

    moderation_service.grant_moderator_role(mod_user.player_id)

    assert moderation_service.is_moderator(normal_user.player_id) is False
    assert moderation_service.is_moderator(mod_user.player_id) is True

    # Normal user cannot access reports queue
    with pytest.raises(PermissionError):
        moderation_service.list_reports(moderator_id=normal_user.player_id)

    # Moderator can list reports
    reports = moderation_service.list_reports(moderator_id=mod_user.player_id)
    assert "items" in reports


def test_resolve_report_suspension_and_audit_trail(
    test_db: Database,
    publication_service: PublicationService,
    moderation_service: ModerationService,
    identity_service: PlayerIdentityService,
):
    creator = identity_service.create("BadCreator")
    reporter = identity_service.create("WatchfulPlayer")
    mod = identity_service.create("SeniorMod")
    moderation_service.grant_moderator_role(mod.player_id)

    quiz_id = setup_quiz(test_db, publication_service, creator.player_id)
    rep = moderation_service.submit_report(
        reporter_id=reporter.player_id,
        target_type="quiz",
        target_id=quiz_id,
        reason="offensive",
    )

    # Moderator suspends quiz
    res = moderation_service.resolve_report(
        moderator_id=mod.player_id,
        report_id=rep["report_id"],
        action="suspend_quiz",
        notes="Violates community standards on civility.",
    )
    assert res["status"] == "resolved"
    assert res["action"] == "suspend_quiz"

    # Confirm public cannot view suspended quiz
    with pytest.raises(PermissionError, match="suspended"):
        publication_service.get_publication(quiz_id)

    # Creator can view suspended quiz and see status
    pub = publication_service.get_publication(quiz_id, viewer_id=creator.player_id)
    assert pub["status"] == "suspended"

    # Audit trail verification
    audit = moderation_service.get_audit_log(moderator_id=mod.player_id)
    assert len(audit) >= 1
    assert any(a["action"] == "suspend_quiz" and a["target_id"] == quiz_id for a in audit)

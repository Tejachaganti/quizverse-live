from __future__ import annotations

import hashlib
import logging
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from quizverse.persistence.authorization import QuizAccessService
from quizverse.persistence.capabilities import CapabilityScope, CapabilityService, _token_hash
from quizverse.persistence.db import Database
from quizverse.persistence.models import AnonymousOwner, CapabilityGrant, Quiz, QuizQuestion
from quizverse.persistence.repositories import QuestionRecord, QuizRecord, QuizRepository
from quizverse.persistence import seeding
from quizverse.persistence.seeding import BUILTIN_BANK_ID, BuiltinSeedConflict, seed_builtin_bank
from quizverse.questions import QUESTIONS


def migrate(database_url: str, project_root: Path) -> None:
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        config = Config(str(project_root / "alembic.ini"))
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture
def migrated_database(tmp_path: Path) -> tuple[Database, Path]:
    project_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "quizverse.sqlite3"
    database_url = f"sqlite:///{db_path.as_posix()}"
    migrate(database_url, project_root)
    return Database(database_url), project_root


def valid_question(**overrides) -> QuestionRecord:
    values = {
        "prompt": "Which planet is known as the Red Planet?",
        "options": ("Venus", "Mars", "Jupiter", "Mercury"),
        "correct_option_index": 1,
        "explanation": "Mars appears reddish from iron oxide on its surface.",
        "category": "science",
        "difficulty": "easy",
        "time_limit_seconds": 15,
    }
    values.update(overrides)
    return QuestionRecord(**values)


def valid_quiz(**overrides) -> QuizRecord:
    values = {"title": "Planet facts", "category": "science"}
    values.update(overrides)
    return QuizRecord(**values)


def test_fresh_migrations_create_schema_and_are_repeatable(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "migrated.sqlite3"
    url = f"sqlite:///{db_path.as_posix()}"
    unopened = Database(url)
    assert not db_path.exists()
    unopened.close()
    migrate(url, project_root)
    migrate(url, project_root)

    engine_db = Database(url)
    try:
        names = set(inspect(engine_db.engine).get_table_names())
        assert {"anonymous_owners", "capability_grants", "quizzes", "quiz_questions", "builtin_seeds", "alembic_version"} <= names
        with engine_db.engine.connect() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
    finally:
        engine_db.close()


def test_quiz_and_questions_persist_across_database_reopen(migrated_database) -> None:
    database, _ = migrated_database
    capability = CapabilityService(database).create_owner()
    created = QuizRepository(database).create_with_questions(
        capability.owner_id,
        valid_quiz(),
        [valid_question()],
    )
    quiz_id = created.id
    database.close()

    reopened = Database(database.engine.url)
    try:
        persisted = QuizRepository(reopened).get(quiz_id)
        assert persisted is not None
        assert persisted.title == "Planet facts"
        assert persisted.owner_id == capability.owner_id
        assert len(persisted.questions) == 1
        question = persisted.questions[0]
        assert (
            question.option_a, question.option_b, question.option_c, question.option_d
        ) == ("Venus", "Mars", "Jupiter", "Mercury")
        assert question.correct_option_index == 1
        assert question.explanation.startswith("Mars appears")
    finally:
        reopened.close()


def test_question_write_rolls_back_quiz_and_all_children_on_constraint_failure(migrated_database) -> None:
    database, _ = migrated_database
    capability = CapabilityService(database).create_owner()
    duplicate_id = "same-question-id"
    with pytest.raises(IntegrityError):
        QuizRepository(database).create_with_questions(
            capability.owner_id,
            valid_quiz(id="atomic-quiz"),
            [valid_question(id=duplicate_id), valid_question(id=duplicate_id, prompt="Different prompt")],
        )
    with database.session() as session:
        assert session.get(Quiz, "atomic-quiz") is None


def test_database_constraints_reject_invalid_rows(migrated_database) -> None:
    database, _ = migrated_database
    capability = CapabilityService(database).create_owner()
    repo = QuizRepository(database)
    with pytest.raises(ValueError, match="four answer options"):
        repo.create_with_questions(capability.owner_id, valid_quiz(), [valid_question(options=("one", "two", "three"))])
    with pytest.raises(ValueError, match="distinct"):
        repo.create_with_questions(capability.owner_id, valid_quiz(), [valid_question(options=("Same", "same", "Else", "Other"))])
    with pytest.raises(ValueError, match="time limit"):
        repo.create_with_questions(capability.owner_id, valid_quiz(default_time_limit_seconds=121), [])

    with database.sessions.begin() as session:
        session.add(Quiz(id="bad", owner_id=capability.owner_id, title="", category="science", difficulty="medium"))
        with pytest.raises(IntegrityError):
            session.flush()


def test_database_enforces_foreign_keys_indexes_and_option_constraints(migrated_database) -> None:
    database, _ = migrated_database
    owner = CapabilityService(database).create_owner()
    quiz = QuizRepository(database).create_with_questions(owner.owner_id, valid_quiz(), [valid_question()])
    question_fields = {
        "quiz_id": quiz.id,
        "position": 0,
        "prompt": "A database constraint test?",
        "option_a": "One",
        "option_b": "Two",
        "option_c": "Three",
        "option_d": "Four",
        "correct_option_index": 0,
        "provenance": "user_authored",
    }
    with pytest.raises(IntegrityError):
        with database.sessions.begin() as session:
            session.add(QuizQuestion(id="duplicate-position", **question_fields))
    with pytest.raises(IntegrityError):
        with database.sessions.begin() as session:
            session.add(QuizQuestion(id="invalid-answer", **{**question_fields, "position": 1, "correct_option_index": 4}))
    with pytest.raises(IntegrityError):
        with database.sessions.begin() as session:
            session.add(QuizQuestion(id="invalid-options", **{**question_fields, "position": 2, "option_a": "One", "option_b": "one"}))
    with pytest.raises(IntegrityError):
        with database.sessions.begin() as session:
            session.add(QuizQuestion(id="invalid-parent", **{**question_fields, "quiz_id": "missing-quiz"}))


def test_owner_capabilities_are_hashed_scoped_rotatable_and_revocable(migrated_database) -> None:
    database, _ = migrated_database
    service = CapabilityService(database)
    issued = service.create_owner()
    assert len(issued.token) >= 40
    assert issued.token not in repr(issued)
    token_hash = hashlib.sha256(issued.token.encode("ascii")).hexdigest()
    with database.session() as session:
        grant = session.scalar(select(CapabilityGrant).where(CapabilityGrant.id == issued.grant_id))
        assert grant is not None
        assert grant.token_hash == token_hash
        assert issued.token not in grant.token_hash
        assert session.get(AnonymousOwner, issued.owner_id) is not None

    assert service.validate(issued.token, scope=CapabilityScope.OWNER) == issued.owner_id
    assert service.validate("x" * 45, scope=CapabilityScope.OWNER) is None
    assert service.validate(issued.token, scope=CapabilityScope.QUIZ_READ, quiz_id="not-a-quiz") is None

    rotated = service.rotate_owner(issued.token)
    assert service.validate(issued.token, scope=CapabilityScope.OWNER) is None
    assert service.validate(rotated.token, scope=CapabilityScope.OWNER) == issued.owner_id

    quiz = QuizRepository(database).create_with_questions(
        issued.owner_id, valid_quiz(status="published"), [valid_question()]
    )
    shared = service.issue_share(rotated.token, quiz.id)
    assert service.validate(shared.token, scope=CapabilityScope.QUIZ_READ, quiz_id=quiz.id) == issued.owner_id
    assert service.validate(shared.token, scope=CapabilityScope.QUIZ_EDIT, quiz_id=quiz.id) is None
    assert service.revoke(rotated.token, shared.grant_id) is True
    assert service.validate(shared.token, scope=CapabilityScope.QUIZ_READ, quiz_id=quiz.id) is None
    assert service.revoke(rotated.token, shared.grant_id) is False
    with database.sessions.begin() as session:
        grant = session.get(CapabilityGrant, shared.grant_id)
        grant.expires_at = grant.created_at
    assert service.validate(shared.token, scope=CapabilityScope.QUIZ_READ, quiz_id=quiz.id) is None
    assert service.revoke_owner(rotated.token) is True
    assert service.validate(rotated.token, scope=CapabilityScope.OWNER) is None


def test_concurrent_owner_rotation_from_one_token_has_one_winner(migrated_database) -> None:
    database, _ = migrated_database
    service = CapabilityService(database)
    original = service.create_owner()
    before_update = Barrier(2)

    def synchronize_rotation(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        if statement.lstrip().upper().startswith("UPDATE CAPABILITY_GRANTS"):
            before_update.wait(timeout=10)

    event.listen(database.engine, "before_cursor_execute", synchronize_rotation)

    def rotate_or_lose():
        try:
            return service.rotate_owner(original.token)
        except PermissionError:
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _index: rotate_or_lose(), range(2)))
    finally:
        event.remove(database.engine, "before_cursor_execute", synchronize_rotation)

    winners = [outcome for outcome in outcomes if outcome is not None]
    assert len(winners) == 1
    assert service.validate(original.token, scope=CapabilityScope.OWNER) is None
    assert service.validate(winners[0].token, scope=CapabilityScope.OWNER) == original.owner_id
    with database.session() as session:
        active_owner_grants = session.scalars(
            select(CapabilityGrant).where(
                CapabilityGrant.owner_id == original.owner_id,
                CapabilityGrant.scope == CapabilityScope.OWNER.value,
                CapabilityGrant.revoked_at.is_(None),
            )
        ).all()
        assert len(active_owner_grants) == 1


def test_capability_lifecycle_never_logs_raw_tokens(migrated_database, caplog) -> None:
    database, _ = migrated_database
    caplog.set_level(logging.DEBUG)
    capabilities = CapabilityService(database)
    owner = capabilities.create_owner()
    quiz = QuizRepository(database).create_with_questions(
        owner.owner_id,
        valid_quiz(status="published"),
        [valid_question()],
    )
    share = capabilities.issue_share(owner.token, quiz.id)
    assert capabilities.validate(owner.token, scope=CapabilityScope.OWNER) == owner.owner_id
    assert capabilities.validate(
        share.token, scope=CapabilityScope.QUIZ_READ, quiz_id=quiz.id
    ) == owner.owner_id
    rotated = capabilities.rotate_owner(owner.token)
    assert capabilities.revoke(rotated.token, share.grant_id, quiz_id=quiz.id)
    assert capabilities.revoke_owner(rotated.token)

    for raw_token in (owner.token, share.token, rotated.token):
        assert raw_token not in caplog.text


def test_capability_scope_cannot_be_used_for_another_owners_quiz(migrated_database) -> None:
    database, _ = migrated_database
    service = CapabilityService(database)
    owner_a = service.create_owner()
    owner_b = service.create_owner()
    quiz_b = QuizRepository(database).create_with_questions(owner_b.owner_id, valid_quiz(), [valid_question()])
    with pytest.raises(PermissionError, match="not authorized"):
        service.issue_share(owner_a.token, quiz_b.id)


def test_quiz_access_distinguishes_owner_public_private_and_draft(migrated_database) -> None:
    database, _ = migrated_database
    capabilities = CapabilityService(database)
    access = QuizAccessService(database)
    owner = capabilities.create_owner()
    other_owner = capabilities.create_owner()
    repo = QuizRepository(database)
    draft = repo.create_with_questions(owner.owner_id, valid_quiz(), [valid_question()])
    private = repo.create_with_questions(
        owner.owner_id,
        valid_quiz(title="Private published quiz", status="published"),
        [valid_question()],
    )
    other_private = repo.create_with_questions(
        owner.owner_id,
        valid_quiz(title="Another private quiz", status="published"),
        [valid_question()],
    )
    public = repo.create_with_questions(
        other_owner.owner_id,
        valid_quiz(title="Public quiz", visibility="public", status="published"),
        [valid_question()],
    )

    owned_draft = access.get_owned_quiz(owner.token, draft.id)
    assert owned_draft is not None
    assert owned_draft.questions[0].correct_option_index == 1
    assert access.get_owned_quiz(other_owner.token, draft.id) is None
    assert access.get_playable_quiz(draft.id) is None
    with pytest.raises(PermissionError, match="Only published"):
        capabilities.issue_share(owner.token, draft.id)
    with pytest.raises(PermissionError, match="Only published"):
        capabilities.issue_share(owner.token, draft.id, editable=True)

    # Even if a bad internal caller inserts a read grant directly, validation
    # rejects it for an unpublished quiz.
    draft_read_token = secrets.token_urlsafe(32)
    with database.sessions.begin() as session:
        session.add(
            CapabilityGrant(
                id=secrets.token_hex(16),
                owner_id=owner.owner_id,
                quiz_id=draft.id,
                token_hash=_token_hash(draft_read_token),
                scope=CapabilityScope.QUIZ_READ.value,
            )
        )
    assert capabilities.validate(
        draft_read_token, scope=CapabilityScope.QUIZ_READ, quiz_id=draft.id
    ) is None
    assert access.get_playable_quiz(draft.id, share_capability=draft_read_token) is None

    # Public published quizzes are playable without a capability. The returned
    # dataclass projection has neither the answer key nor explanation fields.
    player_view = access.get_playable_quiz(public.id)
    assert player_view is not None
    player_question = asdict(player_view)["questions"][0]
    assert player_question["options"] == ("Venus", "Mars", "Jupiter", "Mercury")
    assert "correct_option_index" not in player_question
    assert "explanation" not in player_question
    assert "owner_id" not in asdict(player_view)

    # Private published quizzes require a read grant for the exact quiz. An
    # owner capability or a read grant for a different quiz does not substitute.
    assert access.get_playable_quiz(private.id) is None
    assert access.get_playable_quiz(private.id, share_capability=owner.token) is None
    private_share = capabilities.issue_share(owner.token, private.id)
    assert access.get_playable_quiz(private.id, share_capability=private_share.token) is not None
    other_quiz_share = capabilities.issue_share(owner.token, other_private.id)
    assert access.get_playable_quiz(private.id, share_capability=other_quiz_share.token) is None
    assert access.get_editable_quiz(private_share.token, private.id) is None

    edit_share = capabilities.issue_share(owner.token, private.id, editable=True)
    editable = access.get_editable_quiz(edit_share.token, private.id)
    assert editable is not None
    assert editable.questions[0].correct_option_index == 1
    owner_view = access.get_owned_quiz(owner.token, private.id)
    assert owner_view is not None
    assert owner_view.questions[0].correct_option_index == 1


def test_server_game_source_enforces_visibility_and_returns_authoritative_answers(migrated_database) -> None:
    database, _ = migrated_database
    caps = CapabilityService(database)
    access = QuizAccessService(database)
    owner = caps.create_owner()
    other = caps.create_owner()
    private = QuizRepository(database).create_with_questions(
        owner.owner_id, valid_quiz(status="published"), [valid_question()]
    )
    public = QuizRepository(database).create_with_questions(
        other.owner_id,
        valid_quiz(title="Shared public", visibility="public", status="published"),
        [valid_question()],
    )
    with pytest.raises(PermissionError):
        access.game_question_source(quiz_ids=(private.id,))
    assert access.game_question_source(quiz_ids=(private.id,), capability=owner.token)[0].answer == 1
    assert access.game_question_source(quiz_ids=(public.id,))[0].answer == 1
    public_inventory = access.game_question_source(public_library=True)
    assert all(question.id != private.questions[0].id for question in public_inventory)


def test_structured_mode_game_source_authorizes_published_open_and_puzzle_content(migrated_database) -> None:
    database, _ = migrated_database
    caps = CapabilityService(database)
    access = QuizAccessService(database)
    owner = caps.create_owner()
    other = caps.create_owner()
    open_record = valid_question(
        prompt="Name the red planet.", options=None, correct_option_index=None,
        question_format="open_ended", format_payload={"canonical_answer": "Mars", "accepted_aliases": ["the planet Mars"]},
        review_status="reviewed", learning_levels=("teen_challenge",),
    )
    private_open = QuizRepository(database).create_with_questions(
        owner.owner_id, valid_quiz(title="Private open answers", status="published"), [open_record]
    )
    public_open = QuizRepository(database).create_with_questions(
        other.owner_id, valid_quiz(title="Public open answers", visibility="public", status="published"), [open_record]
    )
    with pytest.raises(PermissionError):
        access.game_question_source(quiz_ids=(private_open.id,), question_format="open_ended")
    assert access.game_question_source(
        quiz_ids=(private_open.id,), capability=owner.token, question_format="open_ended"
    )[0].format_payload["canonical_answer"] == "Mars"
    assert access.game_question_source(
        quiz_ids=(public_open.id,), question_format="open_ended"
    )[0].format_payload["canonical_answer"] == "Mars"
    public_listing = access.list_public_quizzes(question_format="open_ended")
    assert {item.id for item in public_listing} == {public_open.id}
    assert public_listing[0].questions == ()

    puzzle_record = valid_question(
        prompt="What number follows 2, 4, 6?", options=None, correct_option_index=None,
        question_format="puzzle", format_payload={
            "puzzle_type": "number-pattern", "solution": "8", "hint": "Add two each time",
            "puzzle_set_id": "sample-set", "room_index": 1, "puzzle_index": 0,
        }, review_status="reviewed", learning_levels=("teen_challenge",),
    )
    public_puzzle = QuizRepository(database).create_with_questions(
        other.owner_id, valid_quiz(title="Public puzzle", visibility="public", status="published"), [puzzle_record]
    )
    sourced = access.game_question_source(quiz_ids=(public_puzzle.id,), question_format="puzzle")
    assert sourced[0].format_payload["solution"] == "8"
    assert access.list_public_quizzes(question_format="puzzle")[0].questions == ()


def test_private_play_access_stops_after_revocation_or_expiry(migrated_database) -> None:
    database, _ = migrated_database
    capabilities = CapabilityService(database)
    access = QuizAccessService(database)
    owner = capabilities.create_owner()
    private = QuizRepository(database).create_with_questions(
        owner.owner_id,
        valid_quiz(status="published"),
        [valid_question()],
    )

    revoked_share = capabilities.issue_share(owner.token, private.id)
    assert access.get_playable_quiz(private.id, share_capability=revoked_share.token) is not None
    assert capabilities.revoke(owner.token, revoked_share.grant_id) is True
    assert access.get_playable_quiz(private.id, share_capability=revoked_share.token) is None

    expired_share = capabilities.issue_share(owner.token, private.id, expires_in_seconds=60)
    with database.sessions.begin() as session:
        grant = session.get(CapabilityGrant, expired_share.grant_id)
        assert grant is not None
        grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert access.get_playable_quiz(private.id, share_capability=expired_share.token) is None

    deleted = QuizRepository(database).create_with_questions(
        owner.owner_id,
        valid_quiz(title="Deleted published quiz", status="published"),
        [valid_question()],
    )
    with database.sessions.begin() as session:
        deleted_row = session.get(Quiz, deleted.id)
        assert deleted_row is not None
        deleted_row.deleted_at = datetime.now(UTC)
    with pytest.raises(PermissionError, match="not authorized"):
        capabilities.issue_share(owner.token, deleted.id)
    assert access.get_playable_quiz(deleted.id) is None


def test_builtin_seed_is_stable_idempotent_and_preserves_provenance(migrated_database) -> None:
    database, _ = migrated_database
    assert seed_builtin_bank(database) is True
    first = QuizRepository(database).get(BUILTIN_BANK_ID)
    assert first is not None
    assert first.owner_id is None
    assert first.provenance == "built_in_reviewed"
    assert first.status == "published"
    assert len(first.questions) == 20
    question_ids = [question.id for question in first.questions]
    assert len(question_ids) == len(set(question_ids))
    assert all(question.provenance == "built_in_reviewed" for question in first.questions)

    assert seed_builtin_bank(database) is False
    second = QuizRepository(database).get(BUILTIN_BANK_ID)
    assert [question.id for question in second.questions] == question_ids


def test_builtin_seed_content_change_requires_new_bank_version_and_preserves_data(
    migrated_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _ = migrated_database
    assert seed_builtin_bank(database) is True
    repository = QuizRepository(database)
    before = repository.get(BUILTIN_BANK_ID)
    assert before is not None
    original_questions = [
        (question.id, question.prompt, question.correct_option_index)
        for question in before.questions
    ]

    changed_bank = (replace(QUESTIONS[0], prompt=QUESTIONS[0].prompt + " (updated)"), *QUESTIONS[1:])
    monkeypatch.setattr(seeding, "QUESTIONS", changed_bank)
    with pytest.raises(BuiltinSeedConflict, match="new versioned built-in bank ID"):
        seed_builtin_bank(database)

    after = repository.get(BUILTIN_BANK_ID)
    assert after is not None
    assert after.title == before.title
    assert [
        (question.id, question.prompt, question.correct_option_index)
        for question in after.questions
    ] == original_questions

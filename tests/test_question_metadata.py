from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select, text

from quizverse.exam_collection_api import install_exam_collection_api
from quizverse.exam_collections import EXAM_COLLECTIONS
from quizverse.persistence.capabilities import CapabilityService
from quizverse.persistence.db import Database
from quizverse.persistence.exam_seeding import seed_exam_collections
from quizverse.persistence.models import ExamCollection, ExamCollectionCategory, QuizQuestion, Tag
from quizverse.persistence.repositories import QuestionRecord, QuizRecord, QuizRepository
from quizverse.persistence.taxonomy_seeding import seed_taxonomy
from quizverse.question_content import is_suitable_for_level, open_answer_matches
from quizverse.quiz_api import QuestionInput
from quizverse.realtime import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_REVISION = "62c48da61a70"


def migrate(database_url: str, revision: str = "head") -> None:
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), revision)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture
def metadata_database(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'metadata.sqlite3').as_posix()}"
    migrate(url)
    database = Database(url)
    try:
        seed_taxonomy(database)
        yield database
    finally:
        database.close()


def test_all_question_formats_validate_and_legacy_mcq_defaults() -> None:
    mcq = QuestionInput.model_validate({
        "prompt": "Pick one", "options": ["A", "B", "C", "D"], "correct_option_index": 2,
    })
    assert mcq.format == "multiple_choice"
    assert mcq.to_record().correct_option_index == 2

    open_ended = QuestionInput.model_validate({
        "prompt": "Name the capital", "format": "open_ended",
        "format_payload": {"canonical_answer": "Paris", "accepted_aliases": ["City of Paris"]},
    })
    puzzle = QuestionInput.model_validate({
        "prompt": "Solve the room clue", "format": "puzzle",
        "format_payload": {"puzzle_type": "logic-grid", "solution": "The blue key opens door 2", "room_metadata": {"room": "west"}},
    })
    sequence = QuestionInput.model_validate({
        "prompt": "Put these steps in order", "format": "sequence",
        "format_payload": {"items": ["Third", "First", "Second"], "correct_order": [1, 2, 0]},
    })
    assert open_ended.to_record().format_payload["canonical_answer"] == "Paris"
    assert puzzle.to_record().format_payload["puzzle_type"] == "logic-grid"
    assert sequence.to_record().format_payload["correct_order"] == [1, 2, 0]


@pytest.mark.parametrize("payload", [
    {"format": "open_ended", "format_payload": {"accepted_aliases": ["Paris"]}},
    {"format": "puzzle", "format_payload": {"puzzle_type": "Not A Slug", "solution": "answer"}},
    {"format": "sequence", "format_payload": {"items": ["A", "B"], "correct_order": [0, 0]}},
])
def test_malformed_format_payloads_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        QuestionInput.model_validate({"prompt": "A bounded prompt", **payload})
    with pytest.raises(ValidationError):
        QuestionInput.model_validate({
            "prompt": "A bounded prompt", "format": "open_ended",
            "format_payload": {"canonical_answer": "x" * 21_000},
        })


def test_open_answer_normalization_and_learning_suitability() -> None:
    payload = {"canonical_answer": "  Café\nNoir ", "accepted_aliases": ["Café Noir"]}
    assert open_answer_matches(payload, "café   noir")
    assert not open_answer_matches(payload, "cafe noir!")
    assert is_suitable_for_level("unreviewed", ["little_explorers"], "little_explorers") is False
    assert is_suitable_for_level("needs_review", ["little_explorers"], "little_explorers") is False
    assert is_suitable_for_level("reviewed", ["little_explorers"], "little_explorers") is True
    assert is_suitable_for_level("unreviewed", ["teen_challenge"], "teen_challenge") is True
    assert is_suitable_for_level("reviewed", [], "college_arena") is False


def test_metadata_validation_rejects_invalid_language_levels_tags_and_dates() -> None:
    common = {"prompt": "Metadata question", "options": ["A", "B", "C", "D"], "correct_option_index": 0}
    valid = QuestionInput.model_validate({
        **common, "language": "en-US", "learning_levels": ["young_learners", "teen_challenge"],
        "tags": ["critical-thinking"], "source_name": "Reference book",
        "source_url": "https://example.test/source", "source_published_at": date(2024, 1, 1),
        "verified_at": date(2025, 1, 1), "review_due_at": date(2026, 1, 1),
    })
    assert valid.language == "en-us"
    assert valid.to_record().learning_levels == ("young_learners", "teen_challenge")

    invalid_cases = [
        {"language": "bad tag"},
        {"learning_levels": ["adult" ]},
        {"learning_levels": ["teen_challenge", "teen_challenge"]},
        {"tags": ["not a slug!"]},
        {"source_url": "javascript:alert(1)"},
        {"verified_at": date(2026, 2, 1), "review_due_at": date(2026, 1, 1)},
        {"review_status": "reviewed"},
    ]
    for metadata in invalid_cases:
        with pytest.raises(ValidationError):
            QuestionInput.model_validate({**common, **metadata})


def test_question_metadata_persists_with_taxonomy_and_tags(metadata_database: Database) -> None:
    owner = CapabilityService(metadata_database).create_owner()
    created = QuizRepository(metadata_database).create_with_questions(
        owner.owner_id,
        QuizRecord(title="Metadata", category="science"),
        [QuestionRecord(
            prompt="Which planet is red?", options=("Venus", "Mars", "Earth", "Jupiter"),
            correct_option_index=1, category_id="category:science-space-astronomy",
            tags=("critical-thinking",), language="en-US",
            learning_levels=("junior_champions", "teen_challenge"), source_name="Source test",
            source_url="https://example.test/source", source_published_at=date(2024, 1, 1),
            verified_at=date(2025, 1, 1), review_due_at=date(2026, 1, 1),
        )],
    )
    with metadata_database.session() as session:
        question = session.scalar(select(QuizQuestion).where(QuizQuestion.quiz_id == created.id))
        assert question is not None
        assert question.category_id == "category:science-space-astronomy"
        assert question.tags[0].slug == "critical-thinking"
        assert question.language == "en-us"
        assert question.review_status == "unreviewed"
        assert question.learning_levels == ["junior_champions", "teen_challenge"]
        assert question.source_name == "Source test"
        assert question.source_published_at == date(2024, 1, 1)


def test_open_ended_author_payload_can_be_published_without_player_answer_leak(metadata_database: Database) -> None:
    app = create_app(database=metadata_database)
    with TestClient(app) as client:
        owner = client.post("/api/quiz-owners").json()["capability"]
        headers = {"Authorization": f"Bearer {owner}"}
        created = client.post("/api/quizzes", headers=headers, json={
                "title": "Open answer draft", "category": "language", "visibility": "public", "questions": [{
                "prompt": "What is the capital of France?", "format": "open_ended",
                "format_payload": {"canonical_answer": "Paris", "accepted_aliases": ["City of Paris"]},
                "language": "en", "learning_levels": ["college_arena"],
            }],
        })
        assert created.status_code == 201, created.text
        quiz_id = created.json()["id"]
        question = created.json()["questions"][0]
        assert question["format"] == "open_ended"
        assert question["options"] is None and question["correct_option_index"] is None
        assert question["format_payload"]["canonical_answer"] == "Paris"
        assert question["review_status"] == "unreviewed"
        assert client.get(f"/api/quizzes/{quiz_id}").status_code == 404
        assert client.get(f"/api/quizzes/{quiz_id}", headers=headers).json()["questions"][0]["format_payload"]["canonical_answer"] == "Paris"
        published = client.post(f"/api/quizzes/{quiz_id}/publish", headers=headers)
        assert published.status_code == 200
        assert published.json()["questions"][0]["format_payload"]["canonical_answer"] == "Paris"
        public_summary = client.get("/api/quizzes?question_format=open_ended").json()["quizzes"]
        assert len(public_summary) == 1 and public_summary[0]["id"] == quiz_id
        assert public_summary[0]["questions"] == []
        assert client.get(f"/api/quizzes/{quiz_id}").status_code == 404
        assert quiz_id not in client.get("/api/quizzes").text


def test_stage_7c_migration_keeps_old_mcq_and_legacy_category(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'legacy-mcq.sqlite3').as_posix()}"
    migrate(url, BASELINE_REVISION)
    database = Database(url)
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    with database.engine.begin() as connection:
        connection.execute(text("INSERT INTO anonymous_owners (id, created_at) VALUES ('legacy-owner', :now)"), {"now": now})
        connection.execute(text("""INSERT INTO quizzes
            (id, owner_id, title, category, difficulty, visibility, status, default_time_limit_seconds,
             provenance, version, created_at, updated_at)
            VALUES ('legacy-quiz', 'legacy-owner', 'Legacy quiz', 'custom-topic', 'medium', 'private', 'draft',
                    15, 'user_authored', 1, :now, :now)"""), {"now": now})
        connection.execute(text("""INSERT INTO quiz_questions
            (id, quiz_id, position, prompt, option_a, option_b, option_c, option_d, correct_option_index,
             category, provenance, created_at, updated_at)
            VALUES ('legacy-question', 'legacy-quiz', 0, 'Old MCQ', 'A', 'B', 'C', 'D', 1,
                    'custom-topic', 'user_authored', :now, :now)"""), {"now": now})
    database.close()

    migrate(url)
    database = Database(url)
    try:
        with database.session() as session:
            question = session.get(QuizQuestion, "legacy-question")
            assert question is not None
            assert (question.option_a, question.option_b, question.option_c, question.option_d) == ("A", "B", "C", "D")
            assert question.correct_option_index == 1
            assert question.question_format == "multiple_choice"
            assert question.format_payload is None
            assert question.category == "custom-topic"
            assert question.category_id is None
            assert question.language == "en" and question.learning_levels == []
            assert question.review_status == "unreviewed"
    finally:
        database.close()


def test_exam_collection_seed_is_idempotent_and_maps_shared_taxonomy(metadata_database: Database) -> None:
    first = seed_exam_collections(metadata_database)
    assert first.collections_added == 9
    assert first.mappings_added > 0
    second = seed_exam_collections(metadata_database)
    assert second.collections_added == second.mappings_added == 0
    with metadata_database.sessions.begin() as session:
        collection = session.get(ExamCollection, "exam:rrb-ntpc")
        assert collection is not None and collection.syllabus_version is None
        assert collection.region is None and collection.source_url is None
        collection.display_name = "RRB NTPC local label"
        mapping_count = session.scalar(select(func.count()).select_from(ExamCollectionCategory))
    third = seed_exam_collections(metadata_database)
    assert third.collections_added == third.mappings_added == 0
    with metadata_database.session() as session:
        assert session.get(ExamCollection, "exam:rrb-ntpc").display_name == "RRB NTPC local label"
        assert session.scalar(select(func.count()).select_from(ExamCollectionCategory)) == mapping_count
        assert session.scalar(select(func.count()).select_from(ExamCollection)) == len(EXAM_COLLECTIONS)


def test_exam_collection_discovery_is_curated_and_excludes_inactive_items(metadata_database: Database) -> None:
    seed_exam_collections(metadata_database)
    app = create_app(database=metadata_database)
    with metadata_database.sessions.begin() as session:
        session.get(ExamCollection, "exam:banking").is_active = False
    with TestClient(app) as client:
        items = client.get("/api/exam-collections")
        assert items.status_code == 200
        assert len(items.json()) == 8
        detail = client.get("/api/exam-collections/rrb-ntpc")
        assert detail.status_code == 200
        collection = detail.json()
        assert collection["id"] == "exam:rrb-ntpc"
        assert collection["syllabus_version"] is None
        assert all("category_id" in item and "slug" in item for item in collection["categories"])
        assert client.get("/api/exam-collections/banking").status_code == 404
        assert client.get("/api/exam-collections/INVALID SLUG").status_code == 422
        assert "correct_option_index" not in detail.text
        assert "questions" not in detail.text

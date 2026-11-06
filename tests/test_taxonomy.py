from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from quizverse.persistence.capabilities import CapabilityService
from quizverse.persistence.db import Database
from quizverse.persistence.models import Category, QuestionTag, Quiz, QuizQuestion, Tag
from quizverse.persistence.repositories import QuestionRecord, QuizRecord, QuizRepository
from quizverse.persistence.taxonomy_seeding import seed_taxonomy
from quizverse.realtime import create_app
from quizverse.taxonomy import CATEGORIES, TAGS, CategoryDefinition, legacy_category_slug, validate_category_definitions

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_REVISION = "9c82d5f795b9"


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
def taxonomy_database(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'taxonomy.sqlite3').as_posix()}"
    migrate(url)
    database = Database(url)
    try:
        yield database
    finally:
        database.close()


def test_taxonomy_definitions_have_all_roots_unique_slugs_and_valid_tree() -> None:
    roots = [category for category in CATEGORIES if category.parent_slug is None]
    assert [item.slug for item in roots] == [
        "technology", "mathematics", "reasoning", "verbal", "science", "general_knowledge",
        "sports", "entertainment", "nature", "culture", "everyday_knowledge",
    ]
    assert [item.display_name for item in roots] == [
        "Technology & Computing", "Mathematics & Quantitative Aptitude",
        "Logical & Analytical Reasoning", "English & Verbal Ability", "Science & Discovery",
        "General Knowledge & Awareness", "Sports & Fitness", "Entertainment & Pop Culture",
        "Nature & Environment", "Culture, Arts & Literature", "Everyday Knowledge & Life Skills",
    ]
    assert len({item.slug for item in CATEGORIES}) == len(CATEGORIES)
    validate_category_definitions()

    with pytest.raises(ValueError, match="unknown parent"):
        validate_category_definitions((CategoryDefinition("child", "Child", "missing"),))
    with pytest.raises(ValueError, match="cycles"):
        validate_category_definitions((
            CategoryDefinition("first", "First", "second"),
            CategoryDefinition("second", "Second", "first"),
        ))
    with pytest.raises(ValueError, match="cycles"):
        validate_category_definitions((CategoryDefinition("self", "Self", "self"),))


def test_legacy_mapping_is_conservative_and_keeps_custom_values_unmapped() -> None:
    assert legacy_category_slug(" Science ", "user_authored") == "science"
    assert legacy_category_slug("general-knowledge", "user_authored") == "general_knowledge"
    assert legacy_category_slug("Space", "built_in_reviewed") == "science-space-astronomy"
    assert legacy_category_slug("General", "built_in_reviewed") == "general_knowledge"
    assert legacy_category_slug("General", "user_authored") is None
    assert legacy_category_slug("custom-exam-topic", "user_authored") is None


def test_migration_and_seeder_preserve_legacy_values_and_report_mapping(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'legacy.sqlite3').as_posix()}"
    migrate(url, BASELINE_REVISION)
    old_database = Database(url)
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    with old_database.engine.begin() as connection:
        connection.execute(text("INSERT INTO anonymous_owners (id, created_at) VALUES ('legacy-owner', :now)"), {"now": now})
        connection.execute(text("""INSERT INTO quizzes
            (id, owner_id, title, category, difficulty, visibility, status, default_time_limit_seconds,
             provenance, version, created_at, updated_at)
            VALUES ('legacy-quiz', 'legacy-owner', 'Legacy quiz', 'History', 'medium', 'private', 'draft',
                    15, 'user_authored', 1, :now, :now)"""), {"now": now})
        connection.execute(text("""INSERT INTO quiz_questions
            (id, quiz_id, position, prompt, option_a, option_b, option_c, option_d, correct_option_index,
             category, provenance, created_at, updated_at)
            VALUES ('legacy-question', 'legacy-quiz', 0, 'Legacy prompt', 'A', 'B', 'C', 'D', 0,
                    'custom-history-topic', 'user_authored', :now, :now)"""), {"now": now})
    old_database.close()

    migrate(url)
    database = Database(url)
    try:
        report = seed_taxonomy(database)
        with database.session() as session:
            quiz = session.get(Quiz, "legacy-quiz")
            question = session.get(QuizQuestion, "legacy-question")
            assert quiz is not None and question is not None
            assert quiz.category == "History"
            assert question.category == "custom-history-topic"
            assert quiz.category_id == "category:general_knowledge-history-civics"
            assert question.category_id is None
        assert report.mapped_values == {"History": 1}
        assert report.unmapped_values == {"custom-history-topic": 1}
    finally:
        database.close()


def test_seed_is_idempotent_additive_and_preserves_manual_metadata(taxonomy_database: Database) -> None:
    first = seed_taxonomy(taxonomy_database)
    assert first.categories_added == len(CATEGORIES)
    assert first.tags_added > 0
    second = seed_taxonomy(taxonomy_database)
    assert second.categories_added == 0
    assert second.tags_added == 0

    with taxonomy_database.sessions.begin() as session:
        science = session.get(Category, "category:science")
        tag = session.get(Tag, "tag:calculation")
        science.display_name = "Locally Reviewed Science"
        tag.is_active = False
    third = seed_taxonomy(taxonomy_database)
    assert third.categories_added == third.tags_added == 0
    with taxonomy_database.session() as session:
        assert session.get(Category, "category:science").display_name == "Locally Reviewed Science"
        assert session.get(Tag, "tag:calculation").is_active is False
        assert session.scalar(select(func.count()).select_from(Category)) == len(CATEGORIES)


def test_category_constraints_reject_invalid_parents_and_cycles(taxonomy_database: Database) -> None:
    seed_taxonomy(taxonomy_database)
    with taxonomy_database.sessions.begin() as session:
        session.add(Category(
            id="category:bad-parent", slug="bad-parent", name_key="taxonomy.category.bad_parent",
            display_name="Bad Parent", parent_id="category:missing",
        ))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    with taxonomy_database.sessions.begin() as session:
        with pytest.raises(IntegrityError):
            session.execute(text(
                "UPDATE categories SET parent_id = :parent WHERE id = :id"
            ), {"parent": "category:science-biology", "id": "category:science"})
        session.rollback()

    with taxonomy_database.sessions.begin() as session:
        with pytest.raises(IntegrityError):
            session.add(Category(
                id="category:self", slug="self", name_key="taxonomy.category.self",
                display_name="Self", parent_id="category:self",
            ))
            session.flush()
        session.rollback()


def test_tags_are_reusable_and_references_protect_tag_rows(taxonomy_database: Database) -> None:
    seed_taxonomy(taxonomy_database)
    owner = CapabilityService(taxonomy_database).create_owner()
    quiz = QuizRepository(taxonomy_database).create_with_questions(
        owner.owner_id,
        QuizRecord(title="Reusable tags", category="science"),
        [
            QuestionRecord("First question?", ("A", "B", "C", "D"), 0),
            QuestionRecord("Second question?", ("E", "F", "G", "H"), 1),
        ],
    )
    with taxonomy_database.sessions.begin() as session:
        questions = list(session.scalars(select(QuizQuestion).where(QuizQuestion.quiz_id == quiz.id)))
        tag = session.get(Tag, "tag:critical-thinking")
        questions[0].tags.append(tag)
        questions[1].tags.append(tag)
    with taxonomy_database.session() as session:
        assert session.scalar(select(func.count()).select_from(QuestionTag).where(
            QuestionTag.tag_id == "tag:critical-thinking"
        )) == 2
        with pytest.raises(IntegrityError):
            session.execute(text("DELETE FROM tags WHERE id = 'tag:critical-thinking'"))


def test_taxonomy_endpoints_return_active_discovery_data_only(taxonomy_database: Database) -> None:
    seed_taxonomy(taxonomy_database)
    owner = CapabilityService(taxonomy_database).create_owner()
    private_quiz = QuizRepository(taxonomy_database).create_with_questions(
        owner.owner_id,
        QuizRecord(title="Private secret quiz", category="custom-private-topic"),
        [QuestionRecord("Hidden answer key prompt", ("A", "B", "C", "D"), 2)],
    )
    with taxonomy_database.sessions.begin() as session:
        session.get(Category, "category:science-biology").is_active = False
        session.get(Tag, "tag:wordplay").is_active = False

    app = create_app(database=taxonomy_database)
    with TestClient(app) as client:
        roots = client.get("/api/categories")
        assert roots.status_code == 200
        tree = roots.json()
        assert len(tree) == 11
        science = next(node for node in tree if node["slug"] == "science")
        assert all(child["slug"] != "science-biology" for child in science["children"])
        topics = client.get("/api/categories/science/topics")
        assert topics.status_code == 200
        assert "science-biology" not in {item["slug"] for item in topics.json()["topics"]}
        tags = client.get("/api/tags", params={"limit": 2, "offset": 0})
        assert tags.status_code == 200
        assert len(tags.json()["items"]) == 2
        assert tags.json()["total"] == len(TAGS) - 1
        assert all(item["slug"] != "wordplay" for item in client.get("/api/tags").json()["items"])
        assert client.get("/api/tags", params={"limit": 101}).status_code == 422
        assert client.get("/api/categories/missing/topics").status_code == 404
        assert client.get("/api/categories/NOT A SLUG/topics").status_code == 422
        response_text = roots.text + topics.text + tags.text
        assert "Private secret quiz" not in response_text
        assert "Hidden answer key prompt" not in response_text
        assert private_quiz.id not in response_text

"""Atomic repositories for persisted quiz metadata and question sets."""

from __future__ import annotations

import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date
from typing import Any, Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from quizverse.persistence.db import Database
from quizverse.persistence.models import AnonymousOwner, Category, Quiz, QuizQuestion, Tag
from quizverse.question_content import validate_format_payload, validate_question_metadata

_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")


@dataclass(frozen=True, slots=True)
class QuestionRecord:
    prompt: str
    options: tuple[str, str, str, str] | None = None
    correct_option_index: int | None = None
    explanation: str | None = None
    category: str | None = None
    difficulty: str | None = None
    time_limit_seconds: int | None = None
    provenance: str = "user_authored"
    id: str | None = None
    question_format: str = "multiple_choice"
    format_payload: dict[str, Any] | None = None
    category_id: str | None = None
    tags: tuple[str, ...] = ()
    language: str = "en"
    learning_levels: tuple[str, ...] = ()
    review_status: str = "unreviewed"
    source_name: str | None = None
    source_url: str | None = None
    source_published_at: date | None = None
    verified_at: date | None = None
    review_due_at: date | None = None


@dataclass(frozen=True, slots=True)
class QuizRecord:
    title: str
    category: str
    difficulty: str = "medium"
    description: str | None = None
    visibility: str = "private"
    status: str = "draft"
    default_time_limit_seconds: int = 15
    provenance: str = "user_authored"
    id: str | None = None
    version: int = 1


def _plain_text(value: object, label: str, maximum: int, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    normalized = unicodedata.normalize("NFC", value).strip()
    if required and not normalized:
        raise ValueError(f"{label} is required.")
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be at most {maximum} characters.")
    if any(unicodedata.category(character) == "Cc" and character not in "\t\n\r" for character in normalized):
        raise ValueError(f"{label} contains unsupported control characters.")
    return normalized or None


def _category(value: str | None, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError("Category must be a normalized text key.")
    normalized = "-".join(value.strip().lower().split())
    if not _CATEGORY.fullmatch(normalized):
        raise ValueError("Category must use 1–50 letters, numbers, hyphens, or underscores.")
    return normalized


def validate_quiz_record(record: QuizRecord) -> QuizRecord:
    title = _plain_text(record.title, "Title", 100)
    description = _plain_text(record.description, "Description", 500, required=False)
    category = _category(record.category)
    if record.difficulty not in {"easy", "medium", "hard"}:
        raise ValueError("Difficulty must be easy, medium, or hard.")
    if record.visibility not in {"private", "public"} or record.status not in {"draft", "published"}:
        raise ValueError("Visibility or quiz status is invalid.")
    if record.provenance not in {"built_in_reviewed", "user_authored", "ai_draft", "ai_assisted"}:
        raise ValueError("Quiz provenance is invalid.")
    if isinstance(record.default_time_limit_seconds, bool) or not 5 <= record.default_time_limit_seconds <= 120:
        raise ValueError("Default time limit must be between 5 and 120 seconds.")
    if record.version < 1:
        raise ValueError("Quiz version must be positive.")
    return QuizRecord(
        title=title or "", category=category or "", difficulty=record.difficulty,
        description=description, visibility=record.visibility, status=record.status,
        default_time_limit_seconds=record.default_time_limit_seconds, provenance=record.provenance,
        id=record.id, version=record.version,
    )


def validate_question_record(record: QuestionRecord) -> QuestionRecord:
    prompt = _plain_text(record.prompt, "Question", 500)
    if record.question_format == "multiple_choice":
        if not isinstance(record.options, tuple) or len(record.options) != 4:
            raise ValueError("A multiple-choice question must have exactly four answer options.")
        options = tuple(_plain_text(option, "Answer option", 200) or "" for option in record.options)
        normalized_options = [unicodedata.normalize("NFC", option).casefold() for option in options]
        if len(set(normalized_options)) != 4:
            raise ValueError("Answer options must be distinct.")
        if isinstance(record.correct_option_index, bool) or record.correct_option_index is None or not 0 <= record.correct_option_index <= 3:
            raise ValueError("Correct option index must be from 0 to 3.")
    else:
        if record.options is not None or record.correct_option_index is not None:
            raise ValueError("Non-MCQ formats must not use legacy options or answer-index fields.")
        options = None
        if record.question_format not in {"open_ended", "puzzle", "sequence"}:
            raise ValueError("Question format is not supported.")
    explanation = _plain_text(record.explanation, "Explanation", 1000, required=False)
    category = _category(record.category, optional=True)
    if record.difficulty is not None and record.difficulty not in {"easy", "medium", "hard"}:
        raise ValueError("Difficulty must be easy, medium, or hard.")
    if record.time_limit_seconds is not None and (
        isinstance(record.time_limit_seconds, bool) or not 5 <= record.time_limit_seconds <= 120
    ):
        raise ValueError("Question time limit must be between 5 and 120 seconds.")
    if record.provenance not in {"built_in_reviewed", "user_authored", "ai_draft", "ai_assisted"}:
        raise ValueError("Question provenance is invalid.")
    format_payload = validate_format_payload(record.question_format, record.format_payload)
    metadata = validate_question_metadata(
        language=record.language,
        learning_levels=list(record.learning_levels),
        source_name=record.source_name,
        source_url=record.source_url,
        source_published_at=record.source_published_at,
        verified_at=record.verified_at,
        review_due_at=record.review_due_at,
        tags=list(record.tags),
        category_id=record.category_id,
    )
    if record.review_status not in {"unreviewed", "reviewed", "needs_review", "expired"}:
        raise ValueError("Question review status is invalid.")
    return QuestionRecord(
        prompt=prompt or "", options=options, correct_option_index=record.correct_option_index,
        explanation=explanation, category=category, difficulty=record.difficulty,
        time_limit_seconds=record.time_limit_seconds, provenance=record.provenance, id=record.id,
        question_format=record.question_format, format_payload=format_payload,
        category_id=metadata["category_id"], tags=tuple(metadata["tags"]),
        language=metadata["language"], learning_levels=tuple(metadata["learning_levels"]),
        review_status=record.review_status, source_name=metadata["source_name"],
        source_url=metadata["source_url"], source_published_at=metadata["source_published_at"],
        verified_at=metadata["verified_at"], review_due_at=metadata["review_due_at"],
    )


def _insert_quiz(session, owner_id: str | None, quiz: QuizRecord, questions: Iterable[QuestionRecord]) -> Quiz:
    quiz = validate_quiz_record(quiz)
    validated_questions = [validate_question_record(question) for question in questions]
    if len(validated_questions) > 200:
        raise ValueError("A quiz cannot contain more than 200 questions.")
    if quiz.status == "published" and not validated_questions:
        raise ValueError("A published quiz must contain at least one valid question.")
    if quiz.status == "published" and any(item.question_format != "multiple_choice" for item in validated_questions):
        raise ValueError("Only multiple-choice questions can be published for current player experiences.")
    if quiz.provenance == "built_in_reviewed" and owner_id is not None:
        raise ValueError("Built-in reviewed quizzes cannot have an anonymous owner.")
    if quiz.provenance != "built_in_reviewed" and owner_id is None:
        raise ValueError("User-created quizzes require an anonymous owner.")
    quiz_id = quiz.id or secrets.token_hex(20)
    if owner_id is not None:
        owner = session.get(AnonymousOwner, owner_id)
        if owner is None or owner.revoked_at is not None:
            raise ValueError("Quiz owner does not exist or has been revoked.")
    row = Quiz(
        id=quiz_id, owner_id=owner_id, title=quiz.title, description=quiz.description,
        category=quiz.category, difficulty=quiz.difficulty, visibility=quiz.visibility,
        status=quiz.status, default_time_limit_seconds=quiz.default_time_limit_seconds,
        provenance=quiz.provenance, version=quiz.version,
        published_at=datetime.now(UTC) if quiz.status == "published" else None,
        # Mark the collection loaded even for an empty draft. Repository callers
        # build immutable projections after commit, when lazy loading is unavailable.
        questions=[],
    )
    session.add(row)
    session.flush()
    for position, question in enumerate(validated_questions):
        row.questions.append(_question_model(session, question, quiz_id, position))
    session.flush()
    return row


def _question_model(session, question: QuestionRecord, quiz_id: str, position: int) -> QuizQuestion:
    if question.category_id is not None:
        category = session.get(Category, question.category_id)
        if category is None or not category.is_active:
            raise ValueError("Question category must reference an active taxonomy category.")
    tags = list(session.scalars(select(Tag).where(Tag.slug.in_(question.tags), Tag.is_active.is_(True)))) if question.tags else []
    if len(tags) != len(question.tags):
        raise ValueError("Every question tag must reference an active taxonomy tag.")
    options = question.options
    return QuizQuestion(
        id=question.id or secrets.token_hex(20),
        quiz_id=quiz_id,
        position=position,
        prompt=question.prompt,
        option_a=options[0] if options else None,
        option_b=options[1] if options else None,
        option_c=options[2] if options else None,
        option_d=options[3] if options else None,
        correct_option_index=question.correct_option_index,
        explanation=question.explanation,
        category=question.category,
        category_id=question.category_id,
        difficulty=question.difficulty,
        time_limit_seconds=question.time_limit_seconds,
        provenance=question.provenance,
        question_format=question.question_format,
        format_payload=question.format_payload,
        language=question.language,
        learning_levels=list(question.learning_levels),
        review_status=question.review_status,
        source_name=question.source_name,
        source_url=question.source_url,
        source_published_at=question.source_published_at,
        verified_at=question.verified_at,
        review_due_at=question.review_due_at,
        tags=tags,
    )


class QuizRepository:
    """Persists quiz records with all child questions in one transaction."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create_with_questions(
        self,
        owner_id: str | None,
        quiz: QuizRecord,
        questions: Iterable[QuestionRecord],
    ) -> Quiz:
        with self._database.sessions.begin() as session:
            return _insert_quiz(session, owner_id, quiz, questions)

    def get(self, quiz_id: str) -> Quiz | None:
        with self._database.session() as session:
            return session.scalar(
                select(Quiz)
                .options(selectinload(Quiz.questions).selectinload(QuizQuestion.tags))
                .where(Quiz.id == quiz_id, Quiz.deleted_at.is_(None))
            )

    def list_public_published(
        self, *, category: str | None = None, difficulty: str | None = None, search: str | None = None, limit: int = 50
    ) -> list[Quiz]:
        statement = (
            select(Quiz)
            .options(selectinload(Quiz.questions))
            .where(
                Quiz.status == "published",
                Quiz.visibility == "public",
                Quiz.deleted_at.is_(None),
                ~Quiz.questions.any(QuizQuestion.question_format != "multiple_choice"),
            )
        )
        if category is not None:
            statement = statement.where(Quiz.category == _category(category))
        if difficulty is not None:
            statement = statement.where(Quiz.difficulty == difficulty)
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(Quiz.title.ilike(pattern))
        statement = statement.order_by(Quiz.title, Quiz.id).limit(min(max(limit, 1), 100))
        with self._database.session() as session:
            return list(session.scalars(statement).all())

    def list_owned(self, owner_id: str, *, limit: int = 100) -> list[Quiz]:
        statement = (
            select(Quiz)
            .options(selectinload(Quiz.questions).selectinload(QuizQuestion.tags))
            .where(Quiz.owner_id == owner_id, Quiz.deleted_at.is_(None))
            .order_by(Quiz.updated_at.desc(), Quiz.id)
            .limit(min(max(limit, 1), 200))
        )
        with self._database.session() as session:
            return list(session.scalars(statement).all())

    def update_with_questions(
        self,
        owner_id: str,
        quiz_id: str,
        *,
        expected_version: int,
        changes: dict[str, object],
        questions: Iterable[QuestionRecord] | None = None,
    ) -> Quiz | None:
        """Atomically update owner-controlled fields and optionally replace questions."""
        allowed_changes = {
            "title", "description", "category", "difficulty", "visibility", "default_time_limit_seconds"
        }
        if set(changes) - allowed_changes:
            raise ValueError("Quiz update contains unsupported fields.")
        with self._database.sessions.begin() as session:
            quiz = session.scalar(
                select(Quiz)
                .options(selectinload(Quiz.questions).selectinload(QuizQuestion.tags))
                .where(Quiz.id == quiz_id, Quiz.deleted_at.is_(None))
                .with_for_update()
            )
            if quiz is None or quiz.owner_id != owner_id:
                return None
            if quiz.version != expected_version:
                raise QuizVersionConflict("Quiz changed since it was loaded. Refresh and try again.")

            merged = {
                "title": quiz.title,
                "description": quiz.description,
                "category": quiz.category,
                "difficulty": quiz.difficulty,
                "visibility": quiz.visibility,
                "status": quiz.status,
                "default_time_limit_seconds": quiz.default_time_limit_seconds,
                "provenance": quiz.provenance,
                "version": quiz.version + 1,
                "id": quiz.id,
            }
            merged.update(changes)
            validated_quiz = validate_quiz_record(QuizRecord(**merged))
            validated_questions = (
                [validate_question_record(question) for question in questions]
                if questions is not None
                else None
            )
            if validated_questions is not None and len(validated_questions) > 200:
                raise ValueError("A quiz cannot contain more than 200 questions.")
            resulting_question_count = (
                len(validated_questions) if validated_questions is not None else len(quiz.questions)
            )
            if quiz.status == "published" and resulting_question_count < 1:
                raise ValueError("A published quiz must contain at least one valid question.")
            resulting_formats = (
                [question.question_format for question in validated_questions]
                if validated_questions is not None
                else [question.question_format for question in quiz.questions]
            )
            if quiz.status == "published" and any(item != "multiple_choice" for item in resulting_formats):
                raise ValueError("Only multiple-choice questions can be published for current player experiences.")

            quiz.title = validated_quiz.title
            quiz.description = validated_quiz.description
            quiz.category = validated_quiz.category
            quiz.difficulty = validated_quiz.difficulty
            quiz.visibility = validated_quiz.visibility
            quiz.default_time_limit_seconds = validated_quiz.default_time_limit_seconds
            quiz.version = validated_quiz.version
            quiz.updated_at = datetime.now(UTC)

            if validated_questions is not None:
                session.execute(delete(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id))
                session.flush()
                session.expire(quiz, ["questions"])
                quiz.questions = [
                    _question_model(session, question, quiz_id, position)
                    for position, question in enumerate(validated_questions)
                ]
            session.flush()
            return quiz

    def set_published(self, owner_id: str, quiz_id: str, *, published: bool) -> Quiz | None:
        with self._database.sessions.begin() as session:
            quiz = session.scalar(
                select(Quiz)
                .options(selectinload(Quiz.questions).selectinload(QuizQuestion.tags))
                .where(Quiz.id == quiz_id, Quiz.deleted_at.is_(None))
                .with_for_update()
            )
            if quiz is None or quiz.owner_id != owner_id:
                return None
            if published and not quiz.questions:
                raise ValueError("A quiz needs at least one question before it can be published.")
            if published and any(question.question_format != "multiple_choice" for question in quiz.questions):
                raise ValueError("Only multiple-choice questions can be published for current player experiences.")
            new_status = "published" if published else "draft"
            if quiz.status != new_status:
                quiz.status = new_status
                quiz.published_at = datetime.now(UTC) if published else None
                quiz.updated_at = datetime.now(UTC)
                quiz.version += 1
            session.flush()
            return quiz

    def soft_delete(self, owner_id: str, quiz_id: str) -> bool:
        with self._database.sessions.begin() as session:
            quiz = session.scalar(
                select(Quiz).where(Quiz.id == quiz_id, Quiz.deleted_at.is_(None)).with_for_update()
            )
            if quiz is None or quiz.owner_id != owner_id:
                return False
            deleted_at = datetime.now(UTC)
            quiz.deleted_at = deleted_at
            quiz.updated_at = deleted_at
            quiz.version += 1
            for grant in quiz.grants:
                if grant.revoked_at is None:
                    grant.revoked_at = deleted_at
            return True


class QuizVersionConflict(Exception):
    """Raised when an owner submits an edit based on a stale quiz version."""

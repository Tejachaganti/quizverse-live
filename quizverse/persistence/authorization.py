"""Authorization-aware quiz access and answer-safe projections for Stage 3 callers.

This module deliberately exposes no HTTP routes and never returns SQLAlchemy ORM
objects. Future API handlers should depend on ``QuizAccessService`` projections,
not call ``QuizRepository.get`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Iterable

from quizverse.persistence.capabilities import (
    CapabilityScope,
    CapabilityService,
    IssuedCapability,
)
from quizverse.persistence.db import Database
from quizverse.persistence.models import Category, ExamCollection, ExamCollectionCategory, Quiz
from sqlalchemy import select
from quizverse.questions import Question
from quizverse.taxonomy import legacy_category_slug, resolve_category_slug
from quizverse.persistence.repositories import (
    QuestionRecord,
    QuizRecord,
    QuizRepository,
)


@dataclass(frozen=True, slots=True)
class PlayerQuestionView:
    """A playable question without its answer key or explanation."""

    id: str
    position: int
    prompt: str
    options: tuple[str, str, str, str]
    category: str
    difficulty: str
    time_limit_seconds: int


@dataclass(frozen=True, slots=True)
class PlayerQuizView:
    """Published quiz fields safe for a player-facing response."""

    id: str
    title: str
    description: str | None
    category: str
    difficulty: str
    default_time_limit_seconds: int
    questions: tuple[PlayerQuestionView, ...]


@dataclass(frozen=True, slots=True)
class AuthorQuestionView:
    """Question detail available only through owner or explicit edit access."""

    id: str
    position: int
    prompt: str
    options: tuple[str, str, str, str] | None
    correct_option_index: int | None
    explanation: str | None
    category: str | None
    difficulty: str | None
    time_limit_seconds: int | None
    provenance: str
    format: str
    format_payload: dict[str, object] | None
    category_id: str | None
    tags: tuple[str, ...]
    language: str
    learning_levels: tuple[str, ...]
    review_status: str
    source_name: str | None
    source_url: str | None
    source_published_at: date | None
    verified_at: date | None
    review_due_at: date | None


@dataclass(frozen=True, slots=True)
class AuthorQuizView:
    """Owner/editor projection, including answer keys for quiz authoring."""

    id: str
    title: str
    description: str | None
    category: str
    difficulty: str
    visibility: str
    status: str
    default_time_limit_seconds: int
    provenance: str
    version: int
    questions: tuple[AuthorQuestionView, ...]


def _player_projection(quiz: Quiz) -> PlayerQuizView:
    if any(question.question_format != "multiple_choice" for question in quiz.questions):
        raise ValueError("This question format is not supported by current player experiences.")
    return PlayerQuizView(
        id=quiz.id,
        title=quiz.title,
        description=quiz.description,
        category=quiz.category,
        difficulty=quiz.difficulty,
        default_time_limit_seconds=quiz.default_time_limit_seconds,
        questions=tuple(
            PlayerQuestionView(
                id=question.id,
                position=question.position,
                prompt=question.prompt,
                options=(question.option_a, question.option_b, question.option_c, question.option_d),  # type: ignore[arg-type]
                category=question.category or quiz.category,
                difficulty=question.difficulty or quiz.difficulty,
                time_limit_seconds=question.time_limit_seconds or quiz.default_time_limit_seconds,
            )
            for question in quiz.questions
        ),
    )


def _author_projection(quiz: Quiz) -> AuthorQuizView:
    return AuthorQuizView(
        id=quiz.id,
        title=quiz.title,
        description=quiz.description,
        category=quiz.category,
        difficulty=quiz.difficulty,
        visibility=quiz.visibility,
        status=quiz.status,
        default_time_limit_seconds=quiz.default_time_limit_seconds,
        provenance=quiz.provenance,
        version=quiz.version,
        questions=tuple(
            AuthorQuestionView(
                id=question.id,
                position=question.position,
                prompt=question.prompt,
                options=(question.option_a, question.option_b, question.option_c, question.option_d)
                if question.option_a is not None and question.option_b is not None
                and question.option_c is not None and question.option_d is not None else None,
                correct_option_index=question.correct_option_index,
                explanation=question.explanation,
                category=question.category,
                difficulty=question.difficulty,
                time_limit_seconds=question.time_limit_seconds,
                provenance=question.provenance,
                format=question.question_format,
                format_payload=question.format_payload,
                category_id=question.category_id,
                tags=tuple(tag.slug for tag in question.tags),
                language=question.language,
                learning_levels=tuple(question.learning_levels or ()),
                review_status=question.review_status,
                source_name=question.source_name,
                source_url=question.source_url,
                source_published_at=question.source_published_at,
                verified_at=question.verified_at,
                review_due_at=question.review_due_at,
            )
            for question in quiz.questions
        ),
    )


def _game_category_id(question, quiz) -> str | None:
    if question.category_id is not None:
        return question.category_id
    slug = legacy_category_slug(question.category or quiz.category, question.provenance)
    return f"category:{slug}" if slug else quiz.category_id


class QuizAccessService:
    """Resolve owner, editor, and player access before serializing quiz records."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._capabilities = CapabilityService(database)
        self._quizzes = QuizRepository(database)

    def create_owner_capability(self) -> IssuedCapability:
        """Bootstrap an account-free quiz owner identity; token is returned once."""
        return self._capabilities.create_owner()

    def create_quiz(
        self,
        owner_token: str,
        quiz: QuizRecord,
        questions: Iterable[QuestionRecord],
        *,
        ai_generated: bool = False,
    ) -> AuthorQuizView | None:
        owner_id = self._capabilities.validate(owner_token, scope=CapabilityScope.OWNER)
        if owner_id is None:
            return None
        if ai_generated:
            # AI drafts require stable FK-backed taxonomy associations. Seed the
            # existing definitions additively before the atomic quiz write.
            from quizverse.persistence.taxonomy_seeding import seed_taxonomy
            seed_taxonomy(self._database)
            question_records = tuple(questions)
            slug = resolve_category_slug(quiz.category)
            if slug is None and question_records:
                resolved_questions = {resolve_category_slug(item.category) for item in question_records}
                resolved_questions.discard(None)
                if len(resolved_questions) == 1:
                    slug = next(iter(resolved_questions))
            if slug is None:
                raise ValueError("AI-generated quizzes must use a supported category.")
            category_id = f"category:{slug}"
            quiz = QuizRecord(
                title=quiz.title, description=quiz.description, category=slug,
                difficulty=quiz.difficulty, visibility=quiz.visibility, status="draft",
                default_time_limit_seconds=quiz.default_time_limit_seconds, provenance="ai_assisted",
                category_id=category_id,
            )
            questions = (
                QuestionRecord(
                    prompt=item.prompt, options=item.options, correct_option_index=item.correct_option_index,
                    explanation=item.explanation, category=item.category, difficulty=item.difficulty,
                    category_id=f"category:{resolve_category_slug(item.category) or slug}",
                    time_limit_seconds=item.time_limit_seconds, provenance="ai_assisted",
                )
                for item in question_records
            )
        created = self._quizzes.create_with_questions(owner_id, quiz, questions)
        return _author_projection(created)

    def update_quiz(
        self,
        owner_token: str,
        quiz_id: str,
        *,
        expected_version: int,
        changes: dict[str, object],
        questions: Iterable[QuestionRecord] | None = None,
    ) -> AuthorQuizView | None:
        owner_id = self._capabilities.validate(owner_token, scope=CapabilityScope.OWNER)
        if owner_id is None:
            return None
        updated = self._quizzes.update_with_questions(
            owner_id,
            quiz_id,
            expected_version=expected_version,
            changes=changes,
            questions=questions,
        )
        return _author_projection(updated) if updated is not None else None

    def set_quiz_published(
        self, owner_token: str, quiz_id: str, *, published: bool
    ) -> AuthorQuizView | None:
        owner_id = self._capabilities.validate(owner_token, scope=CapabilityScope.OWNER)
        if owner_id is None:
            return None
        quiz = self._quizzes.set_published(owner_id, quiz_id, published=published)
        return _author_projection(quiz) if quiz is not None else None

    def delete_quiz(self, owner_token: str, quiz_id: str) -> bool:
        owner_id = self._capabilities.validate(owner_token, scope=CapabilityScope.OWNER)
        if owner_id is None:
            return False
        return self._quizzes.soft_delete(owner_id, quiz_id)

    def duplicate_public_quiz(self, owner_token: str, source_quiz_id: str) -> AuthorQuizView | None:
        owner_id = self._capabilities.validate(owner_token, scope=CapabilityScope.OWNER)
        if owner_id is None:
            return None
        source = self._quizzes.get(source_quiz_id)
        if source is None or source.status != "published" or source.visibility != "public":
            return None
        attribution = f"Duplicated from public quiz {source.id} — {source.title}."
        description = attribution
        if source.description:
            description += f"\n\n{source.description}"
        description = description[:500]
        duplicate = self._quizzes.create_with_questions(
            owner_id,
            QuizRecord(
                title=f"{source.title[:93].rstrip()} (copy)",
                description=description,
                category=source.category,
                difficulty=source.difficulty,
                visibility="private",
                status="draft",
                default_time_limit_seconds=source.default_time_limit_seconds,
                provenance="user_authored",
            ),
            [
                QuestionRecord(
                    prompt=question.prompt,
                    options=(question.option_a, question.option_b, question.option_c, question.option_d),
                    correct_option_index=question.correct_option_index,
                    explanation=question.explanation,
                    category=question.category,
                    difficulty=question.difficulty,
                    time_limit_seconds=question.time_limit_seconds,
                    provenance="user_authored",
                    question_format=question.question_format,
                    format_payload=question.format_payload,
                    category_id=question.category_id,
                    tags=tuple(tag.slug for tag in question.tags),
                    language=question.language,
                    learning_levels=tuple(question.learning_levels or ()),
                    review_status=question.review_status,
                    source_name=question.source_name,
                    source_url=question.source_url,
                    source_published_at=question.source_published_at,
                    verified_at=question.verified_at,
                    review_due_at=question.review_due_at,
                )
                for question in source.questions
            ],
        )
        return _author_projection(duplicate)

    def list_public_quizzes(
        self,
        *,
        category: str | None = None,
        difficulty: str | None = None,
        search: str | None = None,
        limit: int = 50,
        question_format: str = "multiple_choice",
    ) -> tuple[PlayerQuizView, ...]:
        quizzes = self._quizzes.list_public_published(
            category=category, difficulty=difficulty, search=search, limit=limit,
            question_format=question_format,
        )
        if question_format == "multiple_choice":
            return tuple(_player_projection(quiz) for quiz in quizzes)
        # Mode-specific listings reveal only quiz metadata. Answer payloads remain server-side.
        return tuple(
            PlayerQuizView(
                id=quiz.id, title=quiz.title, description=quiz.description, category=quiz.category,
                difficulty=quiz.difficulty, default_time_limit_seconds=quiz.default_time_limit_seconds,
                questions=(),
            )
            for quiz in quizzes
        )

    def list_owned_quizzes(self, owner_token: str) -> tuple[AuthorQuizView, ...] | None:
        owner_id = self._capabilities.validate(owner_token, scope=CapabilityScope.OWNER)
        if owner_id is None:
            return None
        return tuple(_author_projection(quiz) for quiz in self._quizzes.list_owned(owner_id))

    def create_share(
        self,
        owner_token: str,
        quiz_id: str,
        *,
        editable: bool = False,
        expires_in_seconds: int | None = None,
    ) -> IssuedCapability | None:
        owner_id = self._capabilities.validate(owner_token, scope=CapabilityScope.OWNER)
        quiz = self._quizzes.get(quiz_id) if owner_id is not None else None
        if quiz is None or quiz.owner_id != owner_id:
            return None
        return self._capabilities.issue_share(
            owner_token,
            quiz_id,
            editable=editable,
            expires_in_seconds=expires_in_seconds,
        )

    def revoke_share(self, owner_token: str, quiz_id: str, grant_id: str) -> bool:
        owner_id = self._capabilities.validate(owner_token, scope=CapabilityScope.OWNER)
        quiz = self._quizzes.get(quiz_id) if owner_id is not None else None
        if quiz is None or quiz.owner_id != owner_id:
            return False
        return self._capabilities.revoke(owner_token, grant_id, quiz_id=quiz_id)

    def get_owned_quiz(self, owner_token: str, quiz_id: str) -> AuthorQuizView | None:
        """Return authoring details only when the caller owns this quiz."""
        owner_id = self._capabilities.validate(owner_token, scope=CapabilityScope.OWNER)
        if owner_id is None:
            return None
        quiz = self._quizzes.get(quiz_id)
        if quiz is None or quiz.owner_id != owner_id:
            return None
        return _author_projection(quiz)

    def get_editable_quiz(self, capability: str, quiz_id: str) -> AuthorQuizView | None:
        """Return authoring details to the owner or a quiz-specific edit grant."""
        owner_id = self._capabilities.validate(capability, scope=CapabilityScope.OWNER)
        if owner_id is None:
            owner_id = self._capabilities.validate(
                capability, scope=CapabilityScope.QUIZ_EDIT, quiz_id=quiz_id
            )
        if owner_id is None:
            return None
        quiz = self._quizzes.get(quiz_id)
        if quiz is None or quiz.owner_id != owner_id:
            return None
        return _author_projection(quiz)

    def get_playable_quiz(
        self,
        quiz_id: str,
        *,
        share_capability: str | None = None,
    ) -> PlayerQuizView | None:
        """Return published public quizzes or private quizzes with a matching read grant.

        Owner capabilities do not substitute for a player share capability. Drafts
        are never playable through this method, regardless of a forged/stale grant.
        """
        quiz = self._quizzes.get(quiz_id)
        if quiz is None or quiz.status != "published" or quiz.visibility not in {"public", "private"}:
            return None
        if any(question.question_format != "multiple_choice" for question in quiz.questions):
            return None
        if quiz.visibility == "private":
            if share_capability is None:
                return None
            shared_owner_id = self._capabilities.validate(
                share_capability, scope=CapabilityScope.QUIZ_READ, quiz_id=quiz_id
            )
            if shared_owner_id is None or shared_owner_id != quiz.owner_id:
                return None
        return _player_projection(quiz)

    def game_question_source(
        self, *, quiz_ids: tuple[str, ...] = (), capability: str | None = None,
        public_library: bool = False, question_format: str = "multiple_choice"
    ) -> tuple[Question, ...]:
        """Return answer keys only to the server after validating quiz visibility/capabilities."""
        if public_library:
            quizzes = self._quizzes.list_public_published(limit=100, question_format=question_format)
        else:
            if not quiz_ids or len(quiz_ids) > 20 or len(set(quiz_ids)) != len(quiz_ids):
                raise ValueError("Select between 1 and 20 unique quizzes.")
            quizzes = []
            for quiz_id in quiz_ids:
                quiz = self._quizzes.get(quiz_id)
                if quiz is None or quiz.status != "published":
                    raise PermissionError("A selected quiz is unavailable or unpublished.")
                if quiz.visibility == "private":
                    owner_id = self._capabilities.validate(capability or "", scope=CapabilityScope.OWNER)
                    reader_id = self._capabilities.validate(capability or "", scope=CapabilityScope.QUIZ_READ, quiz_id=quiz_id)
                    if quiz.owner_id not in {owner_id, reader_id}:
                        raise PermissionError("A selected private quiz needs its owner or read capability.")
                elif quiz.visibility != "public":
                    raise PermissionError("A selected quiz is not playable.")
                quizzes.append(quiz)
        if not quizzes or any(question.question_format != question_format for quiz in quizzes for question in quiz.questions):
            raise ValueError(f"The selected content must contain only {question_format} questions to play this mode.")
        exam_ids = self._exam_ids_by_category()

        return tuple(
            Question(
                category=question.category or quiz.category,
                prompt=question.prompt,
                options=(question.option_a, question.option_b, question.option_c, question.option_d)
                if question.question_format == "multiple_choice" else ("", "", "", ""),  # type: ignore[arg-type]
                answer=question.correct_option_index if question.correct_option_index is not None else -1,
                id=question.id,
                category_id=_game_category_id(question, quiz),
                difficulty=question.difficulty or quiz.difficulty,
                language=question.language,
                learning_levels=tuple(question.learning_levels or ()),
                review_status=question.review_status,
                question_format=question.question_format,
                exam_collection_ids=exam_ids(_game_category_id(question, quiz)),
                provenance=question.provenance,
                format_payload=question.format_payload,
                explanation=question.explanation,
            )
            for quiz in quizzes for question in quiz.questions
        )

    def built_in_game_questions(self) -> tuple[Question, ...]:
        """Attach active exam suitability mappings to the reviewed built-in bank."""
        from quizverse.questions import QUESTIONS

        exam_ids = self._exam_ids_by_category()
        return tuple(replace(question, exam_collection_ids=exam_ids(question.category_id)) for question in QUESTIONS)

    def _exam_ids_by_category(self):
        with self._database.session() as session:
            mappings = session.execute(
                select(ExamCollectionCategory.category_id, ExamCollectionCategory.collection_id)
                .join(ExamCollection, ExamCollection.id == ExamCollectionCategory.collection_id)
                .where(ExamCollection.is_active.is_(True))
            ).all()
            parents = {category.id: category.parent_id for category in session.scalars(select(Category))}
        collections_by_category: dict[str, set[str]] = {}
        for category_id, collection_id in mappings:
            collections_by_category.setdefault(category_id, set()).add(collection_id)

        def exam_ids(category_id: str | None) -> frozenset[str]:
            found: set[str] = set()
            visited: set[str] = set()
            while category_id is not None and category_id not in visited:
                visited.add(category_id)
                found.update(collections_by_category.get(category_id, ()))
                category_id = parents.get(category_id)
            return frozenset(found)

        return exam_ids

    def get_authorized_quiz(
        self, quiz_id: str, *, capability: str | None = None
    ) -> AuthorQuizView | PlayerQuizView | None:
        """Resolve owner responses separately from safe public/share player reads."""
        if capability is not None:
            owned = self.get_owned_quiz(capability, quiz_id)
            if owned is not None:
                return owned
        return self.get_playable_quiz(quiz_id, share_capability=capability)

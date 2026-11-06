"""Relational schema for saved quizzes and anonymous capability grants."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
    true,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base used by Alembic migrations and persistence tests."""


class Category(Base):
    """Localized display metadata for a stable, hierarchical category slug."""

    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint("id <> parent_id OR parent_id IS NULL", name="ck_category_not_own_parent"),
        CheckConstraint("sort_order >= 0", name="ck_category_sort_order"),
        CheckConstraint("length(slug) BETWEEN 1 AND 80", name="ck_category_slug_length"),
        CheckConstraint("length(display_name) BETWEEN 1 AND 120", name="ck_category_display_name_length"),
        UniqueConstraint("slug", name="uq_category_slug"),
        UniqueConstraint("name_key", name="uq_category_name_key"),
        Index("ix_categories_parent_active_order", "parent_id", "is_active", "sort_order"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name_key: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("categories.id", ondelete="RESTRICT"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    parent: Mapped["Category | None"] = relationship(
        back_populates="children", remote_side="Category.id", foreign_keys=[parent_id]
    )
    children: Mapped[list["Category"]] = relationship(
        back_populates="parent", order_by="(Category.sort_order, Category.slug)"
    )


class Tag(Base):
    """Reusable, localized descriptors that can be attached to quiz questions."""

    __tablename__ = "tags"
    __table_args__ = (
        CheckConstraint("length(slug) BETWEEN 1 AND 80", name="ck_tag_slug_length"),
        CheckConstraint("length(display_name) BETWEEN 1 AND 120", name="ck_tag_display_name_length"),
        UniqueConstraint("slug", name="uq_tag_slug"),
        UniqueConstraint("name_key", name="uq_tag_name_key"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name_key: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    questions: Mapped[list["QuizQuestion"]] = relationship(
        secondary="question_tags", back_populates="tags"
    )


class QuestionTag(Base):
    """Association rows keep reusable tag references explicit and referentially safe."""

    __tablename__ = "question_tags"
    __table_args__ = (Index("ix_question_tags_tag_id", "tag_id"),)

    question_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("quiz_questions.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("tags.id", ondelete="RESTRICT"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AnonymousOwner(Base):
    __tablename__ = "anonymous_owners"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    quizzes: Mapped[list["Quiz"]] = relationship(back_populates="owner")
    grants: Mapped[list["CapabilityGrant"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class Quiz(Base):
    __tablename__ = "quizzes"
    __table_args__ = (
        CheckConstraint("length(trim(title)) BETWEEN 1 AND 100", name="ck_quiz_title_length"),
        CheckConstraint("description IS NULL OR length(description) <= 500", name="ck_quiz_description_length"),
        CheckConstraint("length(category) BETWEEN 1 AND 50", name="ck_quiz_category_length"),
        CheckConstraint("difficulty IN ('easy', 'medium', 'hard')", name="ck_quiz_difficulty"),
        CheckConstraint("visibility IN ('private', 'public')", name="ck_quiz_visibility"),
        CheckConstraint("status IN ('draft', 'published')", name="ck_quiz_status"),
        CheckConstraint("provenance IN ('built_in_reviewed', 'user_authored', 'ai_draft', 'ai_assisted')", name="ck_quiz_provenance"),
        CheckConstraint("default_time_limit_seconds BETWEEN 5 AND 120", name="ck_quiz_default_time"),
        CheckConstraint("version >= 1", name="ck_quiz_version"),
        CheckConstraint(
            "(provenance = 'built_in_reviewed' AND owner_id IS NULL) OR "
            "(provenance <> 'built_in_reviewed' AND owner_id IS NOT NULL)",
            name="ck_quiz_owner_provenance",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("anonymous_owners.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    category_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("categories.id", ondelete="RESTRICT", name="fk_quizzes_category_id_categories"), index=True
    )
    difficulty: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    visibility: Mapped[str] = mapped_column(String(10), nullable=False, default="private")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")
    default_time_limit_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    provenance: Mapped[str] = mapped_column(String(24), nullable=False, default="user_authored")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped[AnonymousOwner | None] = relationship(back_populates="quizzes")
    taxonomy_category: Mapped[Category | None] = relationship(foreign_keys=[category_id])
    questions: Mapped[list["QuizQuestion"]] = relationship(
        back_populates="quiz", cascade="all, delete-orphan", order_by="QuizQuestion.position"
    )
    grants: Mapped[list["CapabilityGrant"]] = relationship(back_populates="quiz")


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"
    __table_args__ = (
        UniqueConstraint("quiz_id", "position", name="uq_question_quiz_position"),
        CheckConstraint("position >= 0", name="ck_question_position"),
        CheckConstraint("length(trim(prompt)) BETWEEN 1 AND 500", name="ck_question_prompt_length"),
        CheckConstraint("length(trim(option_a)) BETWEEN 1 AND 200", name="ck_question_option_a_length"),
        CheckConstraint("length(trim(option_b)) BETWEEN 1 AND 200", name="ck_question_option_b_length"),
        CheckConstraint("length(trim(option_c)) BETWEEN 1 AND 200", name="ck_question_option_c_length"),
        CheckConstraint("length(trim(option_d)) BETWEEN 1 AND 200", name="ck_question_option_d_length"),
        CheckConstraint("correct_option_index BETWEEN 0 AND 3", name="ck_question_answer_index"),
        CheckConstraint("explanation IS NULL OR length(explanation) <= 1000", name="ck_question_explanation_length"),
        CheckConstraint("category IS NULL OR length(category) BETWEEN 1 AND 50", name="ck_question_category_length"),
        CheckConstraint("difficulty IS NULL OR difficulty IN ('easy', 'medium', 'hard')", name="ck_question_difficulty"),
        CheckConstraint("provenance IN ('built_in_reviewed', 'user_authored', 'ai_draft', 'ai_assisted')", name="ck_question_provenance"),
        CheckConstraint("time_limit_seconds IS NULL OR time_limit_seconds BETWEEN 5 AND 120", name="ck_question_time_limit"),
        CheckConstraint(
            "lower(trim(option_a)) <> lower(trim(option_b)) AND "
            "lower(trim(option_a)) <> lower(trim(option_c)) AND "
            "lower(trim(option_a)) <> lower(trim(option_d)) AND "
            "lower(trim(option_b)) <> lower(trim(option_c)) AND "
            "lower(trim(option_b)) <> lower(trim(option_d)) AND "
            "lower(trim(option_c)) <> lower(trim(option_d))",
            name="ck_question_distinct_options",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    quiz_id: Mapped[str] = mapped_column(String(64), ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(String(500), nullable=False)
    option_a: Mapped[str] = mapped_column(String(200), nullable=False)
    option_b: Mapped[str] = mapped_column(String(200), nullable=False)
    option_c: Mapped[str] = mapped_column(String(200), nullable=False)
    option_d: Mapped[str] = mapped_column(String(200), nullable=False)
    correct_option_index: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation: Mapped[str | None] = mapped_column(String(1000))
    category: Mapped[str | None] = mapped_column(String(50))
    category_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("categories.id", ondelete="RESTRICT", name="fk_quiz_questions_category_id_categories"), index=True
    )
    difficulty: Mapped[str | None] = mapped_column(String(10))
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer)
    provenance: Mapped[str] = mapped_column(String(24), nullable=False, default="user_authored")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    quiz: Mapped[Quiz] = relationship(back_populates="questions")
    taxonomy_category: Mapped[Category | None] = relationship(foreign_keys=[category_id])
    tags: Mapped[list[Tag]] = relationship(secondary="question_tags", back_populates="questions")


class CapabilityGrant(Base):
    __tablename__ = "capability_grants"
    __table_args__ = (
        CheckConstraint("scope IN ('owner', 'quiz_read', 'quiz_edit')", name="ck_capability_scope"),
        CheckConstraint(
            "(scope = 'owner' AND quiz_id IS NULL) OR "
            "(scope IN ('quiz_read', 'quiz_edit') AND quiz_id IS NOT NULL)",
            name="ck_capability_scope_resource",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(32), ForeignKey("anonymous_owners.id", ondelete="CASCADE"), nullable=False, index=True)
    quiz_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("quizzes.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped[AnonymousOwner] = relationship(back_populates="grants")
    quiz: Mapped[Quiz | None] = relationship(back_populates="grants")


class BuiltinSeed(Base):
    """Records which immutable built-in bank revision was seeded."""

    __tablename__ = "builtin_seeds"

    bank_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    seeded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

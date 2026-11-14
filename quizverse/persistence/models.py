"""Relational schema for saved quizzes and anonymous capability grants."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    false as sql_false,
    ForeignKey,
    Float,
    JSON,
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


class ExamCollection(Base):
    """Curated collection metadata; syllabus and source fields remain optional."""

    __tablename__ = "exam_collections"
    __table_args__ = (
        CheckConstraint("length(slug) BETWEEN 1 AND 80", name="ck_exam_collection_slug_length"),
        CheckConstraint("length(display_name) BETWEEN 1 AND 120", name="ck_exam_collection_display_name_length"),
        CheckConstraint("description IS NULL OR length(description) <= 1000", name="ck_exam_collection_description_length"),
        UniqueConstraint("slug", name="uq_exam_collection_slug"),
        UniqueConstraint("name_key", name="uq_exam_collection_name_key"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name_key: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    syllabus_version: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(8))
    source_name: Mapped[str | None] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_published_at: Mapped[date | None] = mapped_column(Date)
    reviewed_at: Mapped[date | None] = mapped_column(Date)
    review_due_at: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    taxonomy_mappings: Mapped[list["ExamCollectionCategory"]] = relationship(
        back_populates="collection", order_by="(ExamCollectionCategory.sort_order, ExamCollectionCategory.category_id)"
    )


class ExamCollectionCategory(Base):
    """Ordered taxonomy mapping for a curated exam collection."""

    __tablename__ = "exam_collection_categories"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="ck_exam_mapping_sort_order"),
        UniqueConstraint("collection_id", "sort_order", name="uq_exam_mapping_order"),
        Index("ix_exam_collection_categories_category_id", "category_id"),
    )

    collection_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("exam_collections.id", ondelete="RESTRICT"), primary_key=True
    )
    category_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("categories.id", ondelete="RESTRICT"), primary_key=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)

    collection: Mapped[ExamCollection] = relationship(back_populates="taxonomy_mappings")
    category: Mapped[Category] = relationship()


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
        CheckConstraint("question_format IN ('multiple_choice', 'open_ended', 'puzzle', 'sequence')", name="ck_question_format"),
        CheckConstraint("review_status IN ('unreviewed', 'reviewed', 'needs_review', 'expired')", name="ck_question_review_status"),
        CheckConstraint(
            "(question_format = 'multiple_choice' AND option_a IS NOT NULL AND option_b IS NOT NULL "
            "AND option_c IS NOT NULL AND option_d IS NOT NULL AND correct_option_index IS NOT NULL "
            "AND format_payload IS NULL) OR "
            "(question_format <> 'multiple_choice' AND option_a IS NULL AND option_b IS NULL "
            "AND option_c IS NULL AND option_d IS NULL AND correct_option_index IS NULL "
            "AND format_payload IS NOT NULL)",
            name="ck_question_format_representation",
        ),
        CheckConstraint(
            "format_payload IS NULL OR length(CAST(format_payload AS VARCHAR)) <= 20000",
            name="ck_question_format_payload_size",
        ),
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
    option_a: Mapped[str | None] = mapped_column(String(200))
    option_b: Mapped[str | None] = mapped_column(String(200))
    option_c: Mapped[str | None] = mapped_column(String(200))
    option_d: Mapped[str | None] = mapped_column(String(200))
    correct_option_index: Mapped[int | None] = mapped_column(Integer)
    question_format: Mapped[str] = mapped_column(String(24), nullable=False, default="multiple_choice", server_default="multiple_choice")
    format_payload: Mapped[dict[str, object] | None] = mapped_column(JSON(none_as_null=True))
    explanation: Mapped[str | None] = mapped_column(String(1000))
    category: Mapped[str | None] = mapped_column(String(50))
    category_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("categories.id", ondelete="RESTRICT", name="fk_quiz_questions_category_id_categories"), index=True
    )
    difficulty: Mapped[str | None] = mapped_column(String(10))
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer)
    provenance: Mapped[str] = mapped_column(String(24), nullable=False, default="user_authored")
    language: Mapped[str] = mapped_column(String(35), nullable=False, default="en", server_default="en")
    learning_levels: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unreviewed", server_default="unreviewed")
    source_name: Mapped[str | None] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_published_at: Mapped[date | None] = mapped_column(Date)
    verified_at: Mapped[date | None] = mapped_column(Date)
    review_due_at: Mapped[date | None] = mapped_column(Date)
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


class PlayerProfile(Base):
    """Opt-in pseudonymous identity, independent of quiz-owner capabilities."""

    __tablename__ = "player_profiles"
    __table_args__ = (
        CheckConstraint("length(trim(display_name)) BETWEEN 1 AND 24", name="ck_player_display_name_length"),
        CheckConstraint("status IN ('active', 'disabled', 'anonymized')", name="ck_player_status"),
        CheckConstraint("version >= 1", name="ck_player_version"),
        Index("ix_player_profiles_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    credentials: Mapped[list["PlayerCredential"]] = relationship(back_populates="player", cascade="all, delete-orphan")
    matches: Mapped[list["MatchParticipant"]] = relationship(back_populates="player")
    ratings: Mapped[list["PlayerRating"]] = relationship(back_populates="player", cascade="all, delete-orphan")
    rating_history: Mapped[list["RatingHistory"]] = relationship(back_populates="player", cascade="all, delete-orphan")
    owned_tournaments: Mapped[list["Tournament"]] = relationship(back_populates="owner")
    tournament_entries: Mapped[list["TournamentParticipant"]] = relationship(back_populates="player")
    owned_leagues: Mapped[list["League"]] = relationship(back_populates="owner")
    season_entries: Mapped[list["SeasonParticipant"]] = relationship(back_populates="player")


class PlayerCredential(Base):
    """Hashed, revocable bearer credential for a player profile."""

    __tablename__ = "player_credentials"
    __table_args__ = (
        Index("ix_player_credentials_player_active", "player_id", "revoked_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    player: Mapped[PlayerProfile] = relationship(back_populates="credentials")


class CompetitiveMatch(Base):
    """Immutable server-verified terminal result; casual unbound matches are omitted."""

    __tablename__ = "competitive_matches"
    __table_args__ = (
        CheckConstraint("status = 'completed'", name="ck_competitive_match_status"),
        CheckConstraint("mode IN ('classic', 'speed_challenge', 'team_battle', 'elimination', 'solo_practice', 'quiz_royale', 'power_play', 'bluff_master', 'escape_room')", name="ck_competitive_match_mode"),
        CheckConstraint("result_kind IN ('individual', 'team', 'cooperative', 'practice')", name="ck_match_result_kind"),
        CheckConstraint("length(question_set_hash) = 64", name="ck_match_question_hash_length"),
        CheckConstraint("length(result_digest) = 64", name="ck_match_result_digest_length"),
        Index("ix_competitive_matches_completed_mode", "completed_at", "mode"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    engine_match_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed", server_default="completed")
    result_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    rules_version: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    question_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_context: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    is_rated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_false())
    rating_ineligible_reason: Mapped[str | None] = mapped_column(String(64))
    rated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    participants: Mapped[list["MatchParticipant"]] = relationship(back_populates="match", cascade="all, delete-orphan", order_by="MatchParticipant.seat_index")
    teams: Mapped[list["MatchTeam"]] = relationship(back_populates="match", cascade="all, delete-orphan", order_by="MatchTeam.team_id")
    rating_history: Mapped[list["RatingHistory"]] = relationship(back_populates="match")


class MatchParticipant(Base):
    """A seat-level result. Anonymous guests are represented without a durable identity."""

    __tablename__ = "match_participants"
    __table_args__ = (
        UniqueConstraint("match_id", "seat_index", name="uq_match_participant_seat"),
        UniqueConstraint("match_id", "player_id", name="uq_match_participant_player"),
        CheckConstraint("seat_index >= 0", name="ck_match_seat_index"),
        CheckConstraint("final_score IS NULL OR final_score >= 0", name="ck_match_final_score"),
        CheckConstraint("correct_count IS NULL OR correct_count >= 0", name="ck_match_correct_count"),
        CheckConstraint("answer_count IS NULL OR answer_count >= 0", name="ck_match_answer_count"),
        CheckConstraint("placement IS NULL OR placement >= 1", name="ck_match_placement"),
        CheckConstraint("disconnect_count >= 0", name="ck_match_disconnect_count"),
        CheckConstraint("outcome IN ('win', 'loss', 'draw', 'team_win', 'team_loss', 'team_draw', 'practice', 'cooperative_success', 'cooperative_failure', 'participated')", name="ck_match_participant_outcome"),
        Index("ix_match_participants_player_match", "player_id", "match_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    match_id: Mapped[str] = mapped_column(String(32), ForeignKey("competitive_matches.id", ondelete="RESTRICT"), nullable=False)
    player_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="RESTRICT"))
    seat_index: Mapped[int] = mapped_column(Integer, nullable=False)
    team_id: Mapped[str | None] = mapped_column(String(24))
    final_score: Mapped[int | None] = mapped_column(Integer)
    correct_count: Mapped[int | None] = mapped_column(Integer)
    answer_count: Mapped[int | None] = mapped_column(Integer)
    placement: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    disconnect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reconnect_expired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    forfeited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    match: Mapped[CompetitiveMatch] = relationship(back_populates="participants")
    player: Mapped[PlayerProfile | None] = relationship(back_populates="matches")


class MatchTeam(Base):
    """Team-level official result, kept separate from individual player placements."""

    __tablename__ = "match_teams"
    __table_args__ = (
        UniqueConstraint("match_id", "team_id", name="uq_match_team"),
        CheckConstraint("placement IS NULL OR placement >= 1", name="ck_match_team_placement"),
        CheckConstraint("score >= 0 AND correct_count >= 0 AND response_time_ms >= 0", name="ck_match_team_metrics"),
        CheckConstraint("outcome IN ('win', 'loss', 'draw')", name="ck_match_team_outcome"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    match_id: Mapped[str] = mapped_column(String(32), ForeignKey("competitive_matches.id", ondelete="RESTRICT"), nullable=False)
    team_id: Mapped[str] = mapped_column(String(24), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False)
    response_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    placement: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(8), nullable=False)

    match: Mapped[CompetitiveMatch] = relationship(back_populates="teams")


class PlayerRating(Base):
    """Current competitive rating and record for an active player profile in a given scope."""

    __tablename__ = "player_ratings"
    __table_args__ = (
        UniqueConstraint("player_id", "rating_scope", name="uq_player_rating_scope"),
        CheckConstraint("rating_scope = 'global' OR rating_scope LIKE 'mode:%'", name="ck_rating_scope_format"),
        CheckConstraint("rated_match_count >= 0", name="ck_player_rating_match_count"),
        CheckConstraint("wins >= 0 AND losses >= 0 AND draws >= 0", name="ck_player_rating_record"),
        CheckConstraint("rating >= 0", name="ck_player_rating_non_negative"),
        Index("ix_player_ratings_scope_rating", "rating_scope", "rating"),
        Index("ix_player_ratings_player_scope", "player_id", "rating_scope"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="CASCADE"), nullable=False)
    rating_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False, default=1000, server_default="1000")
    rated_match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    draws: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False, default="elo_v1", server_default="elo_v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    player: Mapped["PlayerProfile"] = relationship(back_populates="ratings")


class RatingHistory(Base):
    """Immutable audit record of a discrete rating adjustment resulting from an eligible match."""

    __tablename__ = "rating_history"
    __table_args__ = (
        UniqueConstraint("player_id", "rating_scope", "match_id", name="uq_rating_history_player_scope_match"),
        Index("ix_rating_history_player_scope_created", "player_id", "rating_scope", "created_at"),
        Index("ix_rating_history_match_scope", "match_id", "rating_scope"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="CASCADE"), nullable=False)
    rating_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    match_id: Mapped[str] = mapped_column(String(32), ForeignKey("competitive_matches.id", ondelete="RESTRICT"), nullable=False)
    prior_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    new_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_score: Mapped[float] = mapped_column(Float, nullable=False)
    expected_score: Mapped[float] = mapped_column(Float, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    player: Mapped["PlayerProfile"] = relationship(back_populates="rating_history")
    match: Mapped["CompetitiveMatch"] = relationship(back_populates="rating_history")


class Tournament(Base):
    """Competitive tournament supporting single-elimination bracket play."""

    __tablename__ = "tournaments"
    __table_args__ = (
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 80", name="ck_tournament_name_length"),
        CheckConstraint("mode IN ('classic', 'speed_challenge', 'elimination', 'power_play')", name="ck_tournament_mode"),
        CheckConstraint("status IN ('draft', 'registration_open', 'registration_closed', 'scheduled', 'in_progress', 'completed', 'cancelled')", name="ck_tournament_status"),
        CheckConstraint("capacity BETWEEN 2 AND 64", name="ck_tournament_capacity"),
        Index("ix_tournaments_status_created", "status", "created_at"),
        Index("ix_tournaments_owner_created", "owner_player_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_player_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    format: Mapped[str] = mapped_column(String(32), default="single_elimination", server_default="single_elimination", nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", server_default="draft", nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=8, server_default="8", nullable=False)
    registration_opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registration_closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rules_version: Mapped[str] = mapped_column(String(32), default="v1.0", server_default="v1.0", nullable=False)
    configuration: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    owner: Mapped[PlayerProfile] = relationship(back_populates="owned_tournaments")
    participants: Mapped[list["TournamentParticipant"]] = relationship(back_populates="tournament", cascade="all, delete-orphan", order_by="TournamentParticipant.seed")
    rounds: Mapped[list["TournamentRound"]] = relationship(back_populates="tournament", cascade="all, delete-orphan", order_by="TournamentRound.round_number")
    matches: Mapped[list["TournamentMatch"]] = relationship(back_populates="tournament", cascade="all, delete-orphan", order_by="TournamentMatch.match_number")
    audit_events: Mapped[list["TournamentAuditEvent"]] = relationship(back_populates="tournament", cascade="all, delete-orphan", order_by="TournamentAuditEvent.timestamp")


class TournamentParticipant(Base):
    """Registered player in a tournament with seeding and tournament placement."""

    __tablename__ = "tournament_participants"
    __table_args__ = (
        UniqueConstraint("tournament_id", "player_id", name="uq_tournament_participant_player"),
        CheckConstraint("seed IS NULL OR seed >= 1", name="ck_tournament_participant_seed"),
        CheckConstraint("final_placement IS NULL OR final_placement >= 1", name="ck_tournament_participant_placement"),
        CheckConstraint("status IN ('registered', 'withdrawn', 'active', 'eliminated', 'champion', 'runner_up')", name="ck_tournament_participant_status"),
        Index("ix_tournament_participants_tourn_status", "tournament_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tournament_id: Mapped[str] = mapped_column(String(32), ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False)
    player_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="RESTRICT"), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    seed: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="registered", server_default="registered", nullable=False)
    final_placement: Mapped[int | None] = mapped_column(Integer)

    tournament: Mapped[Tournament] = relationship(back_populates="participants")
    player: Mapped[PlayerProfile] = relationship(back_populates="tournament_entries")


class TournamentRound(Base):
    """A round of matches in a tournament bracket."""

    __tablename__ = "tournament_rounds"
    __table_args__ = (
        UniqueConstraint("tournament_id", "round_number", name="uq_tournament_round_number"),
        CheckConstraint("round_number >= 1", name="ck_tournament_round_number"),
        CheckConstraint("status IN ('pending', 'in_progress', 'completed')", name="ck_tournament_round_status"),
        Index("ix_tournament_rounds_tourn_num", "tournament_id", "round_number"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tournament_id: Mapped[str] = mapped_column(String(32), ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending", nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tournament: Mapped[Tournament] = relationship(back_populates="rounds")
    matches: Mapped[list["TournamentMatch"]] = relationship(back_populates="round", cascade="all, delete-orphan", order_by="TournamentMatch.match_number")


class TournamentMatch(Base):
    """An individual head-to-head knockout match in a tournament round."""

    __tablename__ = "tournament_matches"
    __table_args__ = (
        UniqueConstraint("round_id", "match_number", name="uq_tournament_round_match"),
        CheckConstraint("match_number >= 1", name="ck_tournament_match_number"),
        CheckConstraint("status IN ('pending', 'scheduled', 'in_progress', 'completed', 'bye', 'forfeited', 'disputed')", name="ck_tournament_match_status"),
        Index("ix_tournament_matches_tourn_round", "tournament_id", "round_id"),
        Index("ix_tournament_matches_official", "official_match_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tournament_id: Mapped[str] = mapped_column(String(32), ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False)
    round_id: Mapped[str] = mapped_column(String(32), ForeignKey("tournament_rounds.id", ondelete="CASCADE"), nullable=False)
    match_number: Mapped[int] = mapped_column(Integer, nullable=False)
    player_one_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="RESTRICT"))
    player_two_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending", nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    official_match_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("competitive_matches.id", ondelete="SET NULL"))
    winner_player_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="RESTRICT"))
    result_reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tournament: Mapped[Tournament] = relationship(back_populates="matches")
    round: Mapped[TournamentRound] = relationship(back_populates="matches")
    player_one: Mapped[PlayerProfile | None] = relationship(foreign_keys=[player_one_id])
    player_two: Mapped[PlayerProfile | None] = relationship(foreign_keys=[player_two_id])
    winner: Mapped[PlayerProfile | None] = relationship(foreign_keys=[winner_player_id])
    official_match: Mapped[CompetitiveMatch | None] = relationship()


class TournamentAuditEvent(Base):
    """Immutable audit trail for all tournament lifecycle actions and decisions."""

    __tablename__ = "tournament_audit_events"
    __table_args__ = (
        Index("ix_tournament_audit_tourn_time", "tournament_id", "timestamp"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tournament_id: Mapped[str] = mapped_column(String(32), ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False)
    actor_player_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(128))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    structured_summary: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)

    tournament: Mapped[Tournament] = relationship(back_populates="audit_events")
    actor: Mapped[PlayerProfile | None] = relationship()


class TournamentFixtureTicket(Base):
    """Short-lived, single-use ticket admitting an authenticated player to a tournament fixture."""

    __tablename__ = "tournament_fixture_tickets"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_tournament_fixture_ticket_hash"),
        Index("ix_tournament_tickets_match_player", "tournament_match_id", "player_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tournament_id: Mapped[str] = mapped_column(String(32), ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False)
    tournament_match_id: Mapped[str] = mapped_column(String(32), ForeignKey("tournament_matches.id", ondelete="CASCADE"), nullable=False)
    player_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    tournament: Mapped[Tournament] = relationship()
    match: Mapped[TournamentMatch] = relationship()
    player: Mapped[PlayerProfile] = relationship()


class League(Base):
    """Competitive league organizing seasonal round-robin competitions."""

    __tablename__ = "leagues"
    __table_args__ = (
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 80", name="ck_league_name_length"),
        CheckConstraint("mode IN ('classic', 'speed_challenge', 'elimination', 'power_play')", name="ck_league_mode"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_league_status"),
        Index("ix_leagues_status_created", "status", "created_at"),
        Index("ix_leagues_owner_created", "owner_player_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_player_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", server_default="active", nullable=False)
    rules_version: Mapped[str] = mapped_column(String(32), default="v1.0", server_default="v1.0", nullable=False)
    configuration: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    owner: Mapped[PlayerProfile] = relationship(back_populates="owned_leagues")
    seasons: Mapped[list["LeagueSeason"]] = relationship(back_populates="league", cascade="all, delete-orphan", order_by="LeagueSeason.season_number")


class LeagueSeason(Base):
    """An individual season within a league with registered participants and round-robin fixtures."""

    __tablename__ = "league_seasons"
    __table_args__ = (
        UniqueConstraint("league_id", "season_number", name="uq_league_season_number"),
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 80", name="ck_season_name_length"),
        CheckConstraint("season_number >= 1", name="ck_season_number"),
        CheckConstraint("status IN ('draft', 'registration_open', 'registration_closed', 'in_progress', 'completed', 'cancelled')", name="ck_season_status"),
        CheckConstraint("capacity BETWEEN 2 AND 32", name="ck_season_capacity"),
        Index("ix_league_seasons_status_created", "status", "created_at"),
        Index("ix_league_seasons_league_num", "league_id", "season_number"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    league_id: Mapped[str] = mapped_column(String(32), ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False)
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", server_default="draft", nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=16, server_default="16", nullable=False)
    registration_opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registration_closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rules_version: Mapped[str] = mapped_column(String(32), default="v1.0", server_default="v1.0", nullable=False)
    configuration: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    league: Mapped[League] = relationship(back_populates="seasons")
    participants: Mapped[list["SeasonParticipant"]] = relationship(back_populates="season", cascade="all, delete-orphan", order_by="SeasonParticipant.registered_at")
    rounds: Mapped[list["SeasonRound"]] = relationship(back_populates="season", cascade="all, delete-orphan", order_by="SeasonRound.round_number")
    fixtures: Mapped[list["SeasonFixture"]] = relationship(back_populates="season", cascade="all, delete-orphan", order_by="SeasonFixture.match_number")
    audit_events: Mapped[list["SeasonAuditEvent"]] = relationship(back_populates="season", cascade="all, delete-orphan", order_by="SeasonAuditEvent.timestamp")


class SeasonParticipant(Base):
    """Registered player in a league season with standings tracking and final placement."""

    __tablename__ = "season_participants"
    __table_args__ = (
        UniqueConstraint("season_id", "player_id", name="uq_season_participant_player"),
        CheckConstraint("seed IS NULL OR seed >= 1", name="ck_season_participant_seed"),
        CheckConstraint("final_placement IS NULL OR final_placement >= 1", name="ck_season_participant_placement"),
        CheckConstraint("status IN ('registered', 'withdrawn', 'active', 'completed', 'disqualified')", name="ck_season_participant_status"),
        Index("ix_season_participants_season_status", "season_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    season_id: Mapped[str] = mapped_column(String(32), ForeignKey("league_seasons.id", ondelete="CASCADE"), nullable=False)
    player_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="RESTRICT"), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    seed: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="registered", server_default="registered", nullable=False)
    final_placement: Mapped[int | None] = mapped_column(Integer)

    season: Mapped[LeagueSeason] = relationship(back_populates="participants")
    player: Mapped[PlayerProfile] = relationship(back_populates="season_entries")


class SeasonRound(Base):
    """A round of fixtures in a league season."""

    __tablename__ = "season_rounds"
    __table_args__ = (
        UniqueConstraint("season_id", "round_number", name="uq_season_round_number"),
        CheckConstraint("round_number >= 1", name="ck_season_round_number"),
        CheckConstraint("status IN ('pending', 'in_progress', 'completed')", name="ck_season_round_status"),
        Index("ix_season_rounds_season_num", "season_id", "round_number"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    season_id: Mapped[str] = mapped_column(String(32), ForeignKey("league_seasons.id", ondelete="CASCADE"), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending", nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    season: Mapped[LeagueSeason] = relationship(back_populates="rounds")
    fixtures: Mapped[list["SeasonFixture"]] = relationship(back_populates="round", cascade="all, delete-orphan", order_by="SeasonFixture.match_number")


class SeasonFixture(Base):
    """An individual head-to-head match fixture between two players in a league round."""

    __tablename__ = "season_fixtures"
    __table_args__ = (
        UniqueConstraint("round_id", "match_number", name="uq_season_round_match"),
        CheckConstraint("match_number >= 1", name="ck_season_fixture_match_number"),
        CheckConstraint("status IN ('pending', 'scheduled', 'in_progress', 'completed', 'bye', 'forfeited', 'disputed', 'cancelled')", name="ck_season_fixture_status"),
        Index("ix_season_fixtures_season_round", "season_id", "round_id"),
        Index("ix_season_fixtures_official", "official_match_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    season_id: Mapped[str] = mapped_column(String(32), ForeignKey("league_seasons.id", ondelete="CASCADE"), nullable=False)
    round_id: Mapped[str] = mapped_column(String(32), ForeignKey("season_rounds.id", ondelete="CASCADE"), nullable=False)
    match_number: Mapped[int] = mapped_column(Integer, nullable=False)
    player_one_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="RESTRICT"))
    player_two_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending", nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    official_match_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("competitive_matches.id", ondelete="SET NULL"))
    winner_player_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="RESTRICT"))
    is_draw: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_false(), nullable=False)
    player_one_score: Mapped[int | None] = mapped_column(Integer)
    player_two_score: Mapped[int | None] = mapped_column(Integer)
    result_reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    season: Mapped[LeagueSeason] = relationship(back_populates="fixtures")
    round: Mapped[SeasonRound] = relationship(back_populates="fixtures")
    player_one: Mapped[PlayerProfile | None] = relationship(foreign_keys=[player_one_id])
    player_two: Mapped[PlayerProfile | None] = relationship(foreign_keys=[player_two_id])
    winner: Mapped[PlayerProfile | None] = relationship(foreign_keys=[winner_player_id])
    official_match: Mapped[CompetitiveMatch | None] = relationship()


class SeasonAuditEvent(Base):
    """Immutable audit trail for all league season lifecycle actions and decisions."""

    __tablename__ = "season_audit_events"
    __table_args__ = (
        Index("ix_season_audit_season_time", "season_id", "timestamp"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    season_id: Mapped[str] = mapped_column(String(32), ForeignKey("league_seasons.id", ondelete="CASCADE"), nullable=False)
    actor_player_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(128))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    structured_summary: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)

    season: Mapped[LeagueSeason] = relationship(back_populates="audit_events")
    actor: Mapped[PlayerProfile | None] = relationship()


class SeasonFixtureTicket(Base):
    """Short-lived, single-use ticket admitting an authenticated player to a season fixture."""

    __tablename__ = "season_fixture_tickets"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_season_fixture_ticket_hash"),
        Index("ix_season_tickets_fixture_player", "fixture_id", "player_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    season_id: Mapped[str] = mapped_column(String(32), ForeignKey("league_seasons.id", ondelete="CASCADE"), nullable=False)
    fixture_id: Mapped[str] = mapped_column(String(32), ForeignKey("season_fixtures.id", ondelete="CASCADE"), nullable=False)
    player_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    season: Mapped[LeagueSeason] = relationship()
    fixture: Mapped[SeasonFixture] = relationship()
    player: Mapped[PlayerProfile] = relationship()


class Friendship(Base):
    """Canonical bi-directional friendship between two authenticated player profiles.

    Enforces canonical ordering (player_one_id < player_two_id) so each friendship is
    represented by exactly one row with a unique pair constraint.
    """

    __tablename__ = "friendships"
    __table_args__ = (
        CheckConstraint("player_one_id < player_two_id", name="ck_friendship_canonical_order"),
        UniqueConstraint("player_one_id", "player_two_id", name="uq_friendship_pair"),
        Index("ix_friendships_p1", "player_one_id"),
        Index("ix_friendships_p2", "player_two_id"),
        Index("ix_friendships_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    player_one_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="CASCADE"), nullable=False)
    player_two_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    player_one: Mapped[PlayerProfile] = relationship(foreign_keys=[player_one_id])
    player_two: Mapped[PlayerProfile] = relationship(foreign_keys=[player_two_id])


class FriendRequest(Base):
    """A directional friend request from one player to another."""

    __tablename__ = "friend_requests"
    __table_args__ = (
        CheckConstraint("sender_id != recipient_id", name="ck_friend_request_distinct"),
        CheckConstraint("status IN ('pending', 'accepted', 'rejected', 'cancelled')", name="ck_friend_request_status"),
        Index("ix_friend_requests_recipient_status", "recipient_id", "status"),
        Index("ix_friend_requests_sender_status", "sender_id", "status"),
        Index("ix_friend_requests_pair_status", "sender_id", "recipient_id", "status"),
        Index("ix_friend_requests_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sender_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="CASCADE"), nullable=False)
    recipient_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    sender: Mapped[PlayerProfile] = relationship(foreign_keys=[sender_id])
    recipient: Mapped[PlayerProfile] = relationship(foreign_keys=[recipient_id])


class PlayerBlock(Base):
    """A unilateral block relationship suppressing social interactions in both directions."""

    __tablename__ = "player_blocks"
    __table_args__ = (
        CheckConstraint("blocker_id != blocked_id", name="ck_player_block_distinct"),
        UniqueConstraint("blocker_id", "blocked_id", name="uq_player_block"),
        Index("ix_player_blocks_blocker", "blocker_id"),
        Index("ix_player_blocks_blocked", "blocked_id"),
        Index("ix_player_blocks_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    blocker_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="CASCADE"), nullable=False)
    blocked_id: Mapped[str] = mapped_column(String(32), ForeignKey("player_profiles.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    blocker: Mapped[PlayerProfile] = relationship(foreign_keys=[blocker_id])
    blocked: Mapped[PlayerProfile] = relationship(foreign_keys=[blocked_id])



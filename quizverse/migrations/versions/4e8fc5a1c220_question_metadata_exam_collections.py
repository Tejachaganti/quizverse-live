"""Add question metadata/formats and curated exam collections.

Revision ID: 4e8fc5a1c220
Revises: 62c48da61a70
"""

from alembic import op
import sqlalchemy as sa


revision = "4e8fc5a1c220"
down_revision = "62c48da61a70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("quiz_questions") as batch:
        for column in ("option_a", "option_b", "option_c", "option_d", "correct_option_index"):
            batch.alter_column(column, existing_nullable=False, nullable=True)
        batch.add_column(sa.Column("question_format", sa.String(length=24), server_default="multiple_choice", nullable=False))
        batch.add_column(sa.Column("format_payload", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("language", sa.String(length=35), server_default="en", nullable=False))
        batch.add_column(sa.Column("learning_levels", sa.JSON(), server_default=sa.text("'[]'"), nullable=False))
        batch.add_column(sa.Column("review_status", sa.String(length=16), server_default="unreviewed", nullable=False))
        batch.add_column(sa.Column("source_name", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("source_url", sa.String(length=1000), nullable=True))
        batch.add_column(sa.Column("source_published_at", sa.Date(), nullable=True))
        batch.add_column(sa.Column("verified_at", sa.Date(), nullable=True))
        batch.add_column(sa.Column("review_due_at", sa.Date(), nullable=True))
        batch.create_check_constraint(
            "ck_question_format",
            "question_format IN ('multiple_choice', 'open_ended', 'puzzle', 'sequence')",
        )
        batch.create_check_constraint(
            "ck_question_review_status", "review_status IN ('unreviewed', 'reviewed', 'needs_review', 'expired')"
        )
        batch.create_check_constraint(
            "ck_question_format_representation",
            "(question_format = 'multiple_choice' AND option_a IS NOT NULL AND option_b IS NOT NULL "
            "AND option_c IS NOT NULL AND option_d IS NOT NULL AND correct_option_index IS NOT NULL "
            "AND format_payload IS NULL) OR "
            "(question_format <> 'multiple_choice' AND option_a IS NULL AND option_b IS NULL "
            "AND option_c IS NULL AND option_d IS NULL AND correct_option_index IS NULL "
            "AND format_payload IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_question_format_payload_size",
            "format_payload IS NULL OR length(CAST(format_payload AS VARCHAR)) <= 20000",
        )

    op.create_index("ix_quiz_questions_question_format", "quiz_questions", ["question_format"], unique=False)
    op.create_index("ix_quiz_questions_review_status", "quiz_questions", ["review_status"], unique=False)
    op.create_index("ix_quiz_questions_language", "quiz_questions", ["language"], unique=False)

    op.create_table(
        "exam_collections",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name_key", sa.String(length=160), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("syllabus_version", sa.String(length=100), nullable=True),
        sa.Column("region", sa.String(length=8), nullable=True),
        sa.Column("source_name", sa.String(length=200), nullable=True),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("source_published_at", sa.Date(), nullable=True),
        sa.Column("reviewed_at", sa.Date(), nullable=True),
        sa.Column("review_due_at", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(slug) BETWEEN 1 AND 80", name="ck_exam_collection_slug_length"),
        sa.CheckConstraint("length(display_name) BETWEEN 1 AND 120", name="ck_exam_collection_display_name_length"),
        sa.CheckConstraint("description IS NULL OR length(description) <= 1000", name="ck_exam_collection_description_length"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_exam_collection_slug"),
        sa.UniqueConstraint("name_key", name="uq_exam_collection_name_key"),
    )
    op.create_table(
        "exam_collection_categories",
        sa.Column("collection_id", sa.String(length=128), nullable=False),
        sa.Column("category_id", sa.String(length=128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.CheckConstraint("sort_order >= 0", name="ck_exam_mapping_sort_order"),
        sa.ForeignKeyConstraint(["collection_id"], ["exam_collections.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("collection_id", "category_id"),
        sa.UniqueConstraint("collection_id", "sort_order", name="uq_exam_mapping_order"),
    )
    op.create_index(
        "ix_exam_collection_categories_category_id", "exam_collection_categories", ["category_id"], unique=False
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM quiz_questions WHERE question_format <> 'multiple_choice'")):
        raise RuntimeError("Cannot downgrade while non-MCQ question records exist; export or remove them explicitly first.")
    op.drop_index("ix_exam_collection_categories_category_id", table_name="exam_collection_categories")
    op.drop_table("exam_collection_categories")
    op.drop_table("exam_collections")
    op.drop_index("ix_quiz_questions_language", table_name="quiz_questions")
    op.drop_index("ix_quiz_questions_review_status", table_name="quiz_questions")
    op.drop_index("ix_quiz_questions_question_format", table_name="quiz_questions")
    with op.batch_alter_table("quiz_questions") as batch:
        for constraint in (
            "ck_question_format_payload_size", "ck_question_format_representation",
            "ck_question_review_status", "ck_question_format",
        ):
            batch.drop_constraint(constraint, type_="check")
        for column in (
            "review_due_at", "verified_at", "source_published_at", "source_url", "source_name",
            "review_status", "learning_levels", "language", "format_payload", "question_format",
        ):
            batch.drop_column(column)
        for column in ("option_a", "option_b", "option_c", "option_d", "correct_option_index"):
            batch.alter_column(column, existing_nullable=True, nullable=False)

"""Add hierarchical categories and reusable question tags.

Revision ID: 62c48da61a70
Revises: 9c82d5f795b9
"""

from alembic import op
import sqlalchemy as sa


revision = "62c48da61a70"
down_revision = "9c82d5f795b9"
branch_labels = None
depends_on = None


def _create_cycle_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            """CREATE TRIGGER trg_categories_prevent_cycle
            BEFORE UPDATE OF parent_id ON categories
            WHEN NEW.parent_id IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT, 'category hierarchy cycle')
                WHERE NEW.parent_id = NEW.id OR EXISTS (
                    WITH RECURSIVE ancestors(id) AS (
                        SELECT NEW.parent_id
                        UNION
                        SELECT categories.parent_id
                        FROM categories JOIN ancestors ON categories.id = ancestors.id
                        WHERE categories.parent_id IS NOT NULL
                    )
                    SELECT 1 FROM ancestors WHERE id = NEW.id
                );
            END"""
        )
    elif dialect == "postgresql":
        op.execute(
            """CREATE FUNCTION prevent_category_cycle() RETURNS trigger AS $$
            DECLARE creates_cycle boolean;
            BEGIN
                IF NEW.parent_id IS NULL THEN
                    RETURN NEW;
                END IF;
                WITH RECURSIVE ancestors(id) AS (
                    SELECT NEW.parent_id
                    UNION
                    SELECT categories.parent_id
                    FROM categories JOIN ancestors ON categories.id = ancestors.id
                    WHERE categories.parent_id IS NOT NULL
                )
                SELECT EXISTS(SELECT 1 FROM ancestors WHERE id = NEW.id)
                INTO creates_cycle;
                IF creates_cycle THEN
                    RAISE EXCEPTION 'category hierarchy cycle';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql"""
        )
        op.execute(
            """CREATE TRIGGER trg_categories_prevent_cycle
            BEFORE INSERT OR UPDATE ON categories
            FOR EACH ROW EXECUTE FUNCTION prevent_category_cycle()"""
        )


def _drop_cycle_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_categories_prevent_cycle")
    elif dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_categories_prevent_cycle ON categories")
        op.execute("DROP FUNCTION IF EXISTS prevent_category_cycle()")


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name_key", sa.String(length=160), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("parent_id", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id <> parent_id OR parent_id IS NULL", name="ck_category_not_own_parent"),
        sa.CheckConstraint("sort_order >= 0", name="ck_category_sort_order"),
        sa.CheckConstraint("length(slug) BETWEEN 1 AND 80", name="ck_category_slug_length"),
        sa.CheckConstraint("length(display_name) BETWEEN 1 AND 120", name="ck_category_display_name_length"),
        sa.ForeignKeyConstraint(["parent_id"], ["categories.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_category_slug"),
        sa.UniqueConstraint("name_key", name="uq_category_name_key"),
    )
    op.create_index("ix_categories_parent_id", "categories", ["parent_id"], unique=False)
    op.create_index(
        "ix_categories_parent_active_order", "categories", ["parent_id", "is_active", "sort_order"]
    )

    op.create_table(
        "tags",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name_key", sa.String(length=160), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(slug) BETWEEN 1 AND 80", name="ck_tag_slug_length"),
        sa.CheckConstraint("length(display_name) BETWEEN 1 AND 120", name="ck_tag_display_name_length"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_tag_slug"),
        sa.UniqueConstraint("name_key", name="uq_tag_name_key"),
    )

    with op.batch_alter_table("quizzes") as batch:
        batch.add_column(sa.Column("category_id", sa.String(length=128), nullable=True))
        batch.create_foreign_key(
            "fk_quizzes_category_id_categories", "categories", ["category_id"], ["id"], ondelete="RESTRICT"
        )
    op.create_index("ix_quizzes_category_id", "quizzes", ["category_id"], unique=False)
    with op.batch_alter_table("quiz_questions") as batch:
        batch.add_column(sa.Column("category_id", sa.String(length=128), nullable=True))
        batch.create_foreign_key(
            "fk_quiz_questions_category_id_categories", "categories", ["category_id"], ["id"], ondelete="RESTRICT"
        )
    op.create_index("ix_quiz_questions_category_id", "quiz_questions", ["category_id"], unique=False)

    op.create_table(
        "question_tags",
        sa.Column("question_id", sa.String(length=64), nullable=False),
        sa.Column("tag_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["question_id"], ["quiz_questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("question_id", "tag_id"),
    )
    op.create_index("ix_question_tags_tag_id", "question_tags", ["tag_id"], unique=False)
    _create_cycle_guard()


def downgrade() -> None:
    _drop_cycle_guard()
    op.drop_index("ix_question_tags_tag_id", table_name="question_tags")
    op.drop_table("question_tags")
    op.drop_index("ix_quiz_questions_category_id", table_name="quiz_questions")
    with op.batch_alter_table("quiz_questions") as batch:
        batch.drop_constraint("fk_quiz_questions_category_id_categories", type_="foreignkey")
        batch.drop_column("category_id")
    op.drop_index("ix_quizzes_category_id", table_name="quizzes")
    with op.batch_alter_table("quizzes") as batch:
        batch.drop_constraint("fk_quizzes_category_id_categories", type_="foreignkey")
        batch.drop_column("category_id")
    op.drop_table("tags")
    op.drop_index("ix_categories_parent_active_order", table_name="categories")
    op.drop_index("ix_categories_parent_id", table_name="categories")
    op.drop_table("categories")

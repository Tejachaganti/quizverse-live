"""Add AI intelligence and personalized learning tables.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""

from alembic import op
import sqlalchemy as sa


revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. question_quality_assessments table
    op.create_table(
        "question_quality_assessments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("question_id", sa.String(length=64), nullable=True),
        sa.Column("quiz_id", sa.String(length=32), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=False),
        sa.Column("clarity_score", sa.Float(), nullable=False),
        sa.Column("ambiguity_score", sa.Float(), nullable=False),
        sa.Column("answer_consistency_score", sa.Float(), nullable=False),
        sa.Column("explanation_score", sa.Float(), nullable=False),
        sa.Column("duplicate_risk", sa.String(length=32), nullable=False),
        sa.Column("difficulty_confidence", sa.Float(), nullable=False),
        sa.Column("topic_relevance", sa.Float(), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("review_required", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("issues_json", sa.Text(), nullable=True),
        sa.Column("improvements_json", sa.Text(), nullable=True),
        sa.Column("assessed_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["quiz_id"], ["quizzes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qqa_question_id", "question_quality_assessments", ["question_id"], unique=False)
    op.create_index("ix_qqa_quiz_id", "question_quality_assessments", ["quiz_id"], unique=False)
    op.create_index("ix_qqa_prompt_hash", "question_quality_assessments", ["prompt_hash"], unique=False)
    op.create_index("ix_qqa_validation_status", "question_quality_assessments", ["validation_status"], unique=False)

    # 2. player_topic_performances table
    op.create_table(
        "player_topic_performances",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("category_id", sa.String(length=100), nullable=False),
        sa.Column("category_name", sa.String(length=120), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("correct_count", sa.Integer(), nullable=False),
        sa.Column("accuracy_pct", sa.Float(), nullable=False),
        sa.Column("avg_response_time_ms", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trend", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "category_id", "difficulty", name="uq_player_topic_perf"),
    )
    op.create_index("ix_player_topic_perf_player", "player_topic_performances", ["player_id"], unique=False)

    # 3. player_learning_paths table
    op.create_table(
        "player_learning_paths",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("target_category", sa.String(length=100), nullable=False),
        sa.Column("target_subcategory", sa.String(length=100), nullable=True),
        sa.Column("recommended_difficulty", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress_pct", sa.Float(), nullable=False),
        sa.Column("total_steps", sa.Integer(), nullable=False),
        sa.Column("completed_steps", sa.Integer(), nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_player_learning_paths_player", "player_learning_paths", ["player_id"], unique=False)

    # 4. learning_path_steps table
    op.create_table(
        "learning_path_steps",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("path_id", sa.String(length=32), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("activity_type", sa.String(length=32), nullable=False),
        sa.Column("target_quiz_id", sa.String(length=32), nullable=True),
        sa.Column("target_category", sa.String(length=100), nullable=True),
        sa.Column("target_difficulty", sa.String(length=32), nullable=True),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.Column("is_completed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["path_id"], ["player_learning_paths.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_quiz_id"], ["quizzes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path_id", "step_number", name="uq_learning_path_step"),
    )
    op.create_index("ix_learning_path_steps_path", "learning_path_steps", ["path_id"], unique=False)

    # 5. player_learning_reports table
    op.create_table(
        "player_learning_reports",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("report_type", sa.String(length=32), nullable=False),
        sa.Column("time_window", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("generated_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_player_learning_reports_player", "player_learning_reports", ["player_id"], unique=False)
    op.create_index("ix_player_learning_reports_created", "player_learning_reports", ["player_id", "created_at"], unique=False)

    # 6. quiz_recommendation_records table
    op.create_table(
        "quiz_recommendation_records",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("quiz_id", sa.String(length=32), nullable=True),
        sa.Column("quiz_title", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("difficulty", sa.String(length=32), nullable=False),
        sa.Column("reason_type", sa.String(length=64), nullable=False),
        sa.Column("reason_text", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("is_dismissed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["quiz_id"], ["quizzes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quiz_rec_player", "quiz_recommendation_records", ["player_id"], unique=False)
    op.create_index("ix_quiz_rec_quiz", "quiz_recommendation_records", ["quiz_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_quiz_rec_quiz", table_name="quiz_recommendation_records")
    op.drop_index("ix_quiz_rec_player", table_name="quiz_recommendation_records")
    op.drop_table("quiz_recommendation_records")

    op.drop_index("ix_player_learning_reports_created", table_name="player_learning_reports")
    op.drop_index("ix_player_learning_reports_player", table_name="player_learning_reports")
    op.drop_table("player_learning_reports")

    op.drop_index("ix_learning_path_steps_path", table_name="learning_path_steps")
    op.drop_table("learning_path_steps")

    op.drop_index("ix_player_learning_paths_player", table_name="player_learning_paths")
    op.drop_table("player_learning_paths")

    op.drop_index("ix_player_topic_perf_player", table_name="player_topic_performances")
    op.drop_table("player_topic_performances")

    op.drop_index("ix_qqa_validation_status", table_name="question_quality_assessments")
    op.drop_index("ix_qqa_prompt_hash", table_name="question_quality_assessments")
    op.drop_index("ix_qqa_quiz_id", table_name="question_quality_assessments")
    op.drop_index("ix_qqa_question_id", table_name="question_quality_assessments")
    op.drop_table("question_quality_assessments")

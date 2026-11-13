"""Add persistent competitive Elo rankings and rating history.

Revision ID: a7d1e8c3b5f2
Revises: 8f4a7c2d1b60
"""

from alembic import op
import sqlalchemy as sa


revision = "a7d1e8c3b5f2"
down_revision = "8f4a7c2d1b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Additive columns on competitive_matches
    with op.batch_alter_table("competitive_matches") as batch_op:
        batch_op.add_column(
            sa.Column("is_rated", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.add_column(
            sa.Column("rating_ineligible_reason", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("rated_at", sa.DateTime(timezone=True), nullable=True)
        )

    # 2. player_ratings table
    op.create_table(
        "player_ratings",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("rating_scope", sa.String(length=32), nullable=False),
        sa.Column("rating", sa.Integer(), server_default="1000", nullable=False),
        sa.Column("rated_match_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("wins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("losses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("draws", sa.Integer(), server_default="0", nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), server_default="elo_v1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "rating_scope", name="uq_player_rating_scope"),
        sa.CheckConstraint("rating_scope = 'global' OR rating_scope LIKE 'mode:%'", name="ck_rating_scope_format"),
        sa.CheckConstraint("rated_match_count >= 0", name="ck_player_rating_match_count"),
        sa.CheckConstraint("wins >= 0 AND losses >= 0 AND draws >= 0", name="ck_player_rating_record"),
        sa.CheckConstraint("rating >= 0", name="ck_player_rating_non_negative"),
    )
    op.create_index("ix_player_ratings_scope_rating", "player_ratings", ["rating_scope", "rating"], unique=False)
    op.create_index("ix_player_ratings_player_scope", "player_ratings", ["player_id", "rating_scope"], unique=False)

    # 3. rating_history table
    op.create_table(
        "rating_history",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("rating_scope", sa.String(length=32), nullable=False),
        sa.Column("match_id", sa.String(length=32), nullable=False),
        sa.Column("prior_rating", sa.Integer(), nullable=False),
        sa.Column("rating_delta", sa.Integer(), nullable=False),
        sa.Column("new_rating", sa.Integer(), nullable=False),
        sa.Column("actual_score", sa.Float(), nullable=False),
        sa.Column("expected_score", sa.Float(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["match_id"], ["competitive_matches.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "rating_scope", "match_id", name="uq_rating_history_player_scope_match"),
    )
    op.create_index("ix_rating_history_player_scope_created", "rating_history", ["player_id", "rating_scope", "created_at"], unique=False)
    op.create_index("ix_rating_history_match_scope", "rating_history", ["match_id", "rating_scope"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_rating_history_match_scope", table_name="rating_history")
    op.drop_index("ix_rating_history_player_scope_created", table_name="rating_history")
    op.drop_table("rating_history")
    op.drop_index("ix_player_ratings_player_scope", table_name="player_ratings")
    op.drop_index("ix_player_ratings_scope_rating", table_name="player_ratings")
    op.drop_table("player_ratings")
    with op.batch_alter_table("competitive_matches") as batch_op:
        batch_op.drop_column("rated_at")
        batch_op.drop_column("rating_ineligible_reason")
        batch_op.drop_column("is_rated")

"""Add creator profiles, quiz publications, community discovery, reviews, collections, and moderation tables.

Revision ID: c1d2e3f4a5b6
Revises: b8c9d0e1f2a3
"""

from alembic import op
import sqlalchemy as sa


revision = "c1d2e3f4a5b6"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. creator_profiles
    op.create_table(
        "creator_profiles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("creator_display_name", sa.String(length=32), nullable=False),
        sa.Column("creator_bio", sa.String(length=1000), nullable=True),
        sa.Column("creator_avatar_url", sa.String(length=255), nullable=True),
        sa.Column("creator_banner_url", sa.String(length=255), nullable=True),
        sa.Column("creator_specializations", sa.JSON(), nullable=False),
        sa.Column("creator_visibility", sa.String(length=16), nullable=False, server_default="public"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("published_quiz_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_plays", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_reviews", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("average_rating", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("follower_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("following_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("creator_visibility IN ('public', 'private')", name="ck_creator_visibility"),
    )
    op.create_index("ix_creator_visibility", "creator_profiles", ["creator_visibility"], unique=False)

    # 2. quiz_publications
    op.create_table(
        "quiz_publications",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("quiz_id", sa.String(length=64), nullable=False),
        sa.Column("creator_id", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False, server_default="public"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="published"),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("suspension_reason", sa.Text(), nullable=True),
        sa.Column("share_slug", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_plays", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_reviews", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rating_sum", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("average_rating", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("validation_summary", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["quiz_id"], ["quizzes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["creator_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quiz_id", name="uq_quiz_publications_quiz_id"),
        sa.UniqueConstraint("share_slug", name="uq_quiz_publications_share_slug"),
        sa.CheckConstraint("visibility IN ('private', 'unlisted', 'public')", name="ck_pub_visibility"),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_review', 'published', 'rejected', 'archived', 'suspended')",
            name="ck_pub_status",
        ),
    )
    op.create_index("ix_quiz_pub_status_vis", "quiz_publications", ["status", "visibility"], unique=False)
    op.create_index("ix_quiz_pub_creator", "quiz_publications", ["creator_id"], unique=False)
    op.create_index("ix_quiz_pub_rating", "quiz_publications", ["average_rating"], unique=False)
    op.create_index("ix_quiz_pub_plays", "quiz_publications", ["total_plays"], unique=False)
    op.create_index("ix_quiz_pub_published_at", "quiz_publications", ["published_at"], unique=False)

    # 3. quiz_plays
    op.create_table(
        "quiz_plays",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("quiz_id", sa.String(length=64), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["quiz_id"], ["quizzes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quiz_play_player_quiz", "quiz_plays", ["player_id", "quiz_id"], unique=False)
    op.create_index("ix_quiz_play_completed_at", "quiz_plays", ["completed_at"], unique=False)

    # 4. creator_follows
    op.create_table(
        "creator_follows",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("follower_id", sa.String(length=32), nullable=False),
        sa.Column("creator_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["follower_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["creator_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("follower_id", "creator_id", name="uq_creator_follower"),
        sa.CheckConstraint("follower_id <> creator_id", name="ck_cannot_follow_self"),
    )
    op.create_index("ix_creator_follow_creator", "creator_follows", ["creator_id"], unique=False)
    op.create_index("ix_creator_follow_follower", "creator_follows", ["follower_id"], unique=False)

    # 5. quiz_reviews
    op.create_table(
        "quiz_reviews",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("quiz_id", sa.String(length=64), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("review_text", sa.String(length=1000), nullable=True),
        sa.Column("creator_response", sa.String(length=1000), nullable=True),
        sa.Column("creator_responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("helpful_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["quiz_id"], ["quizzes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quiz_id", "player_id", name="uq_quiz_review_player"),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_quiz_review_rating"),
        sa.CheckConstraint("review_text IS NULL OR length(review_text) <= 1000", name="ck_quiz_review_length"),
    )
    op.create_index("ix_quiz_reviews_quiz", "quiz_reviews", ["quiz_id"], unique=False)
    op.create_index("ix_quiz_reviews_player", "quiz_reviews", ["player_id"], unique=False)
    op.create_index("ix_quiz_reviews_created", "quiz_reviews", ["created_at"], unique=False)

    # 6. quiz_review_helpful
    op.create_table(
        "quiz_review_helpful",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("review_id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["quiz_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id", "player_id", name="uq_review_helpful_player"),
    )
    op.create_index("ix_review_helpful_review", "quiz_review_helpful", ["review_id"], unique=False)

    # 7. quiz_favorites
    op.create_table(
        "quiz_favorites",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("quiz_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["quiz_id"], ["quizzes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "quiz_id", name="uq_player_quiz_favorite"),
    )
    op.create_index("ix_quiz_favorites_player", "quiz_favorites", ["player_id"], unique=False)
    op.create_index("ix_quiz_favorites_quiz", "quiz_favorites", ["quiz_id"], unique=False)

    # 8. quiz_collections
    op.create_table(
        "quiz_collections",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("visibility", sa.String(length=16), nullable=False, server_default="public"),
        sa.Column("cover_url", sa.String(length=255), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("visibility IN ('private', 'unlisted', 'public')", name="ck_col_visibility"),
    )
    op.create_index("ix_quiz_collections_owner", "quiz_collections", ["owner_id"], unique=False)
    op.create_index("ix_quiz_collections_vis", "quiz_collections", ["visibility"], unique=False)

    # 9. quiz_collection_items
    op.create_table(
        "quiz_collection_items",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("collection_id", sa.String(length=32), nullable=False),
        sa.Column("quiz_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["collection_id"], ["quiz_collections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["quiz_id"], ["quizzes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("collection_id", "quiz_id", name="uq_col_item_quiz"),
    )
    op.create_index("ix_col_items_collection", "quiz_collection_items", ["collection_id"], unique=False)
    op.create_index("ix_col_items_quiz", "quiz_collection_items", ["quiz_id"], unique=False)

    # 10. community_reports
    op.create_table(
        "community_reports",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("reporter_id", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("assigned_moderator_id", sa.String(length=32), nullable=True),
        sa.Column("moderator_notes", sa.Text(), nullable=True),
        sa.Column("resolution", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["reporter_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_moderator_id"], ["player_profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('open', 'under_review', 'action_taken', 'dismissed')", name="ck_report_status"),
        sa.CheckConstraint("target_type IN ('quiz', 'review', 'creator', 'question')", name="ck_report_target_type"),
    )
    op.create_index("ix_comm_reports_target", "community_reports", ["target_type", "target_id"], unique=False)
    op.create_index("ix_comm_reports_status_created", "community_reports", ["status", "created_at"], unique=False)

    # 11. moderation_actions
    op.create_table(
        "moderation_actions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("report_id", sa.String(length=32), nullable=True),
        sa.Column("moderator_id", sa.String(length=32), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["community_reports.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["moderator_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mod_actions_mod", "moderation_actions", ["moderator_id"], unique=False)
    op.create_index("ix_mod_actions_target", "moderation_actions", ["target_type", "target_id"], unique=False)
    op.create_index("ix_mod_actions_created", "moderation_actions", ["created_at"], unique=False)

    # 12. player_roles
    op.create_table(
        "player_roles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("granted_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "role", name="uq_player_role"),
    )
    op.create_index("ix_player_roles_role", "player_roles", ["role"], unique=False)


def downgrade() -> None:
    op.drop_table("player_roles")
    op.drop_table("moderation_actions")
    op.drop_table("community_reports")
    op.drop_table("quiz_collection_items")
    op.drop_table("quiz_collections")
    op.drop_table("quiz_favorites")
    op.drop_table("quiz_review_helpful")
    op.drop_table("quiz_reviews")
    op.drop_table("creator_follows")
    op.drop_table("quiz_plays")
    op.drop_table("quiz_publications")
    op.drop_table("creator_profiles")

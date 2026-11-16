"""Add player notifications and notification preferences tables.

Revision ID: a7b8c9d0e1f2
Revises: f3a6b8c9d1e2
"""

from alembic import op
import sqlalchemy as sa


revision = "a7b8c9d0e1f2"
down_revision = "f3a6b8c9d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. player_notifications table
    op.create_table(
        "player_notifications",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("recipient_id", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=32), nullable=True),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("is_read", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deduplication_key", sa.String(length=128), nullable=True),
        sa.Column("source_event_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["recipient_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["player_profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recipient_id", "deduplication_key", name="uq_player_notification_dedup"),
    )
    op.create_index("ix_player_notifications_recipient_read", "player_notifications", ["recipient_id", "is_read"], unique=False)
    op.create_index("ix_player_notifications_recipient_created", "player_notifications", ["recipient_id", "created_at"], unique=False)
    op.create_index("ix_player_notifications_type", "player_notifications", ["notification_type"], unique=False)
    op.create_index("ix_player_notifications_dedup", "player_notifications", ["recipient_id", "deduplication_key"], unique=False)
    op.create_index("ix_player_notifications_expires", "player_notifications", ["expires_at"], unique=False)

    # 2. notification_preferences table
    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("friend_requests", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("friendship_updates", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("friend_online", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("room_invitations", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("rematch_challenges", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("direct_message_notifications", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("tournament_updates", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("league_updates", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("achievement_notifications", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("rating_milestones", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("personal_best_notifications", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("browser_sound", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("visual_notifications", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", name="uq_notification_preferences_player"),
    )
    op.create_index("ix_notification_preferences_player", "notification_preferences", ["player_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notification_preferences_player", table_name="notification_preferences")
    op.drop_table("notification_preferences")

    op.drop_index("ix_player_notifications_expires", table_name="player_notifications")
    op.drop_index("ix_player_notifications_dedup", table_name="player_notifications")
    op.drop_index("ix_player_notifications_type", table_name="player_notifications")
    op.drop_index("ix_player_notifications_recipient_created", table_name="player_notifications")
    op.drop_index("ix_player_notifications_recipient_read", table_name="player_notifications")
    op.drop_table("player_notifications")

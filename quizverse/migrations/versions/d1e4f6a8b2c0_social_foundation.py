"""Add social foundation tables: friendships, friend requests, and player blocks.

Revision ID: d1e4f6a8b2c0
Revises: c9f3e1a5d7b2
"""

from alembic import op
import sqlalchemy as sa


revision = "d1e4f6a8b2c0"
down_revision = "c9f3e1a5d7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. friendships table
    op.create_table(
        "friendships",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("player_one_id", sa.String(length=32), nullable=False),
        sa.Column("player_two_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("player_one_id < player_two_id", name="ck_friendship_canonical_order"),
        sa.ForeignKeyConstraint(["player_one_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_two_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_one_id", "player_two_id", name="uq_friendship_pair"),
    )
    op.create_index("ix_friendships_p1", "friendships", ["player_one_id"], unique=False)
    op.create_index("ix_friendships_p2", "friendships", ["player_two_id"], unique=False)
    op.create_index("ix_friendships_created", "friendships", ["created_at"], unique=False)

    # 2. friend_requests table
    op.create_table(
        "friend_requests",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("sender_id", sa.String(length=32), nullable=False),
        sa.Column("recipient_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sender_id != recipient_id", name="ck_friend_request_distinct"),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'rejected', 'cancelled')", name="ck_friend_request_status"),
        sa.ForeignKeyConstraint(["sender_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_friend_requests_recipient_status", "friend_requests", ["recipient_id", "status"], unique=False)
    op.create_index("ix_friend_requests_sender_status", "friend_requests", ["sender_id", "status"], unique=False)
    op.create_index("ix_friend_requests_pair_status", "friend_requests", ["sender_id", "recipient_id", "status"], unique=False)
    op.create_index("ix_friend_requests_created", "friend_requests", ["created_at"], unique=False)

    # 3. player_blocks table
    op.create_table(
        "player_blocks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("blocker_id", sa.String(length=32), nullable=False),
        sa.Column("blocked_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("blocker_id != blocked_id", name="ck_player_block_distinct"),
        sa.ForeignKeyConstraint(["blocker_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["blocked_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("blocker_id", "blocked_id", name="uq_player_block"),
    )
    op.create_index("ix_player_blocks_blocker", "player_blocks", ["blocker_id"], unique=False)
    op.create_index("ix_player_blocks_blocked", "player_blocks", ["blocked_id"], unique=False)
    op.create_index("ix_player_blocks_created", "player_blocks", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_player_blocks_created", table_name="player_blocks")
    op.drop_index("ix_player_blocks_blocked", table_name="player_blocks")
    op.drop_index("ix_player_blocks_blocker", table_name="player_blocks")
    op.drop_table("player_blocks")

    op.drop_index("ix_friend_requests_created", table_name="friend_requests")
    op.drop_index("ix_friend_requests_pair_status", table_name="friend_requests")
    op.drop_index("ix_friend_requests_sender_status", table_name="friend_requests")
    op.drop_index("ix_friend_requests_recipient_status", table_name="friend_requests")
    op.drop_table("friend_requests")

    op.drop_index("ix_friendships_created", table_name="friendships")
    op.drop_index("ix_friendships_p2", table_name="friendships")
    op.drop_index("ix_friendships_p1", table_name="friendships")
    op.drop_table("friendships")

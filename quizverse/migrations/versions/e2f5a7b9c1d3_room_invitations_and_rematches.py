"""Add room invitations and rematch challenges tables.

Revision ID: e2f5a7b9c1d3
Revises: d1e4f6a8b2c0
"""

from alembic import op
import sqlalchemy as sa


revision = "e2f5a7b9c1d3"
down_revision = "d1e4f6a8b2c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. room_invitations table
    op.create_table(
        "room_invitations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("inviter_id", sa.String(length=32), nullable=False),
        sa.Column("invitee_id", sa.String(length=32), nullable=False),
        sa.Column("room_code", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("admission_ticket_hash", sa.String(length=64), nullable=True),
        sa.Column("ticket_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ticket_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("inviter_id != invitee_id", name="ck_room_invitation_distinct"),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'declined', 'cancelled', 'expired')", name="ck_room_invitation_status"),
        sa.ForeignKeyConstraint(["inviter_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invitee_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_room_invitations_invitee_status", "room_invitations", ["invitee_id", "status"], unique=False)
    op.create_index("ix_room_invitations_inviter_status", "room_invitations", ["inviter_id", "status"], unique=False)
    op.create_index("ix_room_invitations_room_code", "room_invitations", ["room_code"], unique=False)
    op.create_index("ix_room_invitations_ticket_hash", "room_invitations", ["admission_ticket_hash"], unique=False)
    op.create_index("ix_room_invitations_created", "room_invitations", ["created_at"], unique=False)

    # 2. rematch_challenges table
    op.create_table(
        "rematch_challenges",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("original_match_id", sa.String(length=32), nullable=False),
        sa.Column("challenger_id", sa.String(length=32), nullable=False),
        sa.Column("opponent_id", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resulting_room_code", sa.String(length=16), nullable=True),
        sa.CheckConstraint("challenger_id != opponent_id", name="ck_rematch_distinct"),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'declined', 'cancelled', 'expired')", name="ck_rematch_status"),
        sa.ForeignKeyConstraint(["original_match_id"], ["competitive_matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["challenger_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["opponent_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rematch_opponent_status", "rematch_challenges", ["opponent_id", "status"], unique=False)
    op.create_index("ix_rematch_challenger_status", "rematch_challenges", ["challenger_id", "status"], unique=False)
    op.create_index("ix_rematch_original_match", "rematch_challenges", ["original_match_id"], unique=False)
    op.create_index("ix_rematch_created", "rematch_challenges", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_rematch_created", table_name="rematch_challenges")
    op.drop_index("ix_rematch_original_match", table_name="rematch_challenges")
    op.drop_index("ix_rematch_challenger_status", table_name="rematch_challenges")
    op.drop_index("ix_rematch_opponent_status", table_name="rematch_challenges")
    op.drop_table("rematch_challenges")

    op.drop_index("ix_room_invitations_created", table_name="room_invitations")
    op.drop_index("ix_room_invitations_ticket_hash", table_name="room_invitations")
    op.drop_index("ix_room_invitations_room_code", table_name="room_invitations")
    op.drop_index("ix_room_invitations_inviter_status", table_name="room_invitations")
    op.drop_index("ix_room_invitations_invitee_status", table_name="room_invitations")
    op.drop_table("room_invitations")

"""Add private messaging and chat tables.

Revision ID: f3a6b8c9d1e2
Revises: e2f5a7b9c1d3
"""

from alembic import op
import sqlalchemy as sa


revision = "f3a6b8c9d1e2"
down_revision = "e2f5a7b9c1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. direct_conversations table
    op.create_table(
        "direct_conversations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("player_one_id", sa.String(length=32), nullable=False),
        sa.Column("player_two_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_id", sa.String(length=32), nullable=True),
        sa.Column("player_one_unread_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("player_two_unread_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("player_one_id < player_two_id", name="ck_direct_conversation_canonical_order"),
        sa.ForeignKeyConstraint(["player_one_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_two_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_one_id", "player_two_id", name="uq_direct_conversation_pair"),
    )
    op.create_index("ix_direct_conversations_p1", "direct_conversations", ["player_one_id"], unique=False)
    op.create_index("ix_direct_conversations_p2", "direct_conversations", ["player_two_id"], unique=False)
    op.create_index("ix_direct_conversations_last_msg", "direct_conversations", ["last_message_at"], unique=False)
    op.create_index("ix_direct_conversations_created", "direct_conversations", ["created_at"], unique=False)

    # 2. direct_messages table
    op.create_table(
        "direct_messages",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("conversation_id", sa.String(length=32), nullable=False),
        sa.Column("sender_id", sa.String(length=32), nullable=False),
        sa.Column("message_type", sa.String(length=16), server_default="text", nullable=False),
        sa.Column("content", sa.String(length=2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["direct_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_direct_messages_conv_created", "direct_messages", ["conversation_id", "created_at"], unique=False)
    op.create_index("ix_direct_messages_sender", "direct_messages", ["sender_id"], unique=False)
    op.create_index("ix_direct_messages_created", "direct_messages", ["created_at"], unique=False)

    # 3. direct_message_reactions table
    op.create_table(
        "direct_message_reactions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("message_id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("reaction", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reaction IN ('👍', '❤️', '😂', '🔥', '👏', '😮', '😢')", name="ck_direct_message_reaction_allowed"),
        sa.ForeignKeyConstraint(["message_id"], ["direct_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "player_id", "reaction", name="uq_direct_message_reaction"),
    )
    op.create_index("ix_direct_message_reactions_msg", "direct_message_reactions", ["message_id"], unique=False)
    op.create_index("ix_direct_message_reactions_player", "direct_message_reactions", ["player_id"], unique=False)

    # 4. message_reports table
    op.create_table(
        "message_reports",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("reporter_id", sa.String(length=32), nullable=False),
        sa.Column("message_id", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("details", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reason IN ('harassment', 'spam', 'abuse', 'inappropriate', 'other')", name="ck_message_report_reason"),
        sa.CheckConstraint("status IN ('pending', 'reviewed', 'dismissed', 'actioned')", name="ck_message_report_status"),
        sa.ForeignKeyConstraint(["reporter_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["direct_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reporter_id", "message_id", name="uq_message_report_pair"),
    )
    op.create_index("ix_message_reports_reporter", "message_reports", ["reporter_id"], unique=False)
    op.create_index("ix_message_reports_message", "message_reports", ["message_id"], unique=False)
    op.create_index("ix_message_reports_status", "message_reports", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_message_reports_status", table_name="message_reports")
    op.drop_index("ix_message_reports_message", table_name="message_reports")
    op.drop_index("ix_message_reports_reporter", table_name="message_reports")
    op.drop_table("message_reports")

    op.drop_index("ix_direct_message_reactions_player", table_name="direct_message_reactions")
    op.drop_index("ix_direct_message_reactions_msg", table_name="direct_message_reactions")
    op.drop_table("direct_message_reactions")

    op.drop_index("ix_direct_messages_created", table_name="direct_messages")
    op.drop_index("ix_direct_messages_sender", table_name="direct_messages")
    op.drop_index("ix_direct_messages_conv_created", table_name="direct_messages")
    op.drop_table("direct_messages")

    op.drop_index("ix_direct_conversations_created", table_name="direct_conversations")
    op.drop_index("ix_direct_conversations_last_msg", table_name="direct_conversations")
    op.drop_index("ix_direct_conversations_p2", table_name="direct_conversations")
    op.drop_index("ix_direct_conversations_p1", table_name="direct_conversations")
    op.drop_table("direct_conversations")

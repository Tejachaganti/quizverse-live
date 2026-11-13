"""Add single-elimination tournament MVP tables, brackets, and audit events.

Revision ID: b8e2f9d4c6a1
Revises: a7d1e8c3b5f2
"""

from alembic import op
import sqlalchemy as sa


revision = "b8e2f9d4c6a1"
down_revision = "a7d1e8c3b5f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. tournaments table
    op.create_table(
        "tournaments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_player_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("format", sa.String(length=32), server_default="single_elimination", nullable=False),
        sa.Column("mode", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
        sa.Column("capacity", sa.Integer(), server_default="8", nullable=False),
        sa.Column("registration_opens_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registration_closes_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rules_version", sa.String(length=32), server_default="v1.0", nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 80", name="ck_tournament_name_length"),
        sa.CheckConstraint("mode IN ('classic', 'speed_challenge', 'elimination', 'power_play')", name="ck_tournament_mode"),
        sa.CheckConstraint("status IN ('draft', 'registration_open', 'registration_closed', 'scheduled', 'in_progress', 'completed', 'cancelled')", name="ck_tournament_status"),
        sa.CheckConstraint("capacity BETWEEN 2 AND 64", name="ck_tournament_capacity"),
        sa.ForeignKeyConstraint(["owner_player_id"], ["player_profiles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tournaments_status_created", "tournaments", ["status", "created_at"], unique=False)
    op.create_index("ix_tournaments_owner_created", "tournaments", ["owner_player_id", "created_at"], unique=False)

    # 2. tournament_participants table
    op.create_table(
        "tournament_participants",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tournament_id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="registered", nullable=False),
        sa.Column("final_placement", sa.Integer(), nullable=True),
        sa.CheckConstraint("seed IS NULL OR seed >= 1", name="ck_tournament_participant_seed"),
        sa.CheckConstraint("final_placement IS NULL OR final_placement >= 1", name="ck_tournament_participant_placement"),
        sa.CheckConstraint("status IN ('registered', 'withdrawn', 'active', 'eliminated', 'champion', 'runner_up')", name="ck_tournament_participant_status"),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tournament_id"], ["tournaments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tournament_id", "player_id", name="uq_tournament_participant_player"),
    )
    op.create_index("ix_tournament_participants_tourn_status", "tournament_participants", ["tournament_id", "status"], unique=False)

    # 3. tournament_rounds table
    op.create_table(
        "tournament_rounds",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tournament_id", sa.String(length=32), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("round_number >= 1", name="ck_tournament_round_number"),
        sa.CheckConstraint("status IN ('pending', 'in_progress', 'completed')", name="ck_tournament_round_status"),
        sa.ForeignKeyConstraint(["tournament_id"], ["tournaments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tournament_id", "round_number", name="uq_tournament_round_number"),
    )
    op.create_index("ix_tournament_rounds_tourn_num", "tournament_rounds", ["tournament_id", "round_number"], unique=False)

    # 4. tournament_matches table
    op.create_table(
        "tournament_matches",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tournament_id", sa.String(length=32), nullable=False),
        sa.Column("round_id", sa.String(length=32), nullable=False),
        sa.Column("match_number", sa.Integer(), nullable=False),
        sa.Column("player_one_id", sa.String(length=32), nullable=True),
        sa.Column("player_two_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("official_match_id", sa.String(length=32), nullable=True),
        sa.Column("winner_player_id", sa.String(length=32), nullable=True),
        sa.Column("result_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("match_number >= 1", name="ck_tournament_match_number"),
        sa.CheckConstraint("status IN ('pending', 'scheduled', 'in_progress', 'completed', 'bye', 'forfeited', 'disputed')", name="ck_tournament_match_status"),
        sa.ForeignKeyConstraint(["official_match_id"], ["competitive_matches.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["player_one_id"], ["player_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["player_two_id"], ["player_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["round_id"], ["tournament_rounds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tournament_id"], ["tournaments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["winner_player_id"], ["player_profiles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("round_id", "match_number", name="uq_tournament_round_match"),
    )
    op.create_index("ix_tournament_matches_tourn_round", "tournament_matches", ["tournament_id", "round_id"], unique=False)
    op.create_index("ix_tournament_matches_official", "tournament_matches", ["official_match_id"], unique=False)

    # 5. tournament_audit_events table
    op.create_table(
        "tournament_audit_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tournament_id", sa.String(length=32), nullable=False),
        sa.Column("actor_player_id", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("structured_summary", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["actor_player_id"], ["player_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tournament_id"], ["tournaments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tournament_audit_tourn_time", "tournament_audit_events", ["tournament_id", "timestamp"], unique=False)

    # 6. tournament_fixture_tickets table
    op.create_table(
        "tournament_fixture_tickets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tournament_id", sa.String(length=32), nullable=False),
        sa.Column("tournament_match_id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tournament_id"], ["tournaments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tournament_match_id"], ["tournament_matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_tournament_fixture_ticket_hash"),
    )
    op.create_index("ix_tournament_tickets_match_player", "tournament_fixture_tickets", ["tournament_match_id", "player_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tournament_tickets_match_player", table_name="tournament_fixture_tickets")
    op.drop_table("tournament_fixture_tickets")
    op.drop_index("ix_tournament_audit_tourn_time", table_name="tournament_audit_events")
    op.drop_table("tournament_audit_events")
    op.drop_index("ix_tournament_matches_official", table_name="tournament_matches")
    op.drop_index("ix_tournament_matches_tourn_round", table_name="tournament_matches")
    op.drop_table("tournament_matches")
    op.drop_index("ix_tournament_rounds_tourn_num", table_name="tournament_rounds")
    op.drop_table("tournament_rounds")
    op.drop_index("ix_tournament_participants_tourn_status", table_name="tournament_participants")
    op.drop_table("tournament_participants")
    op.drop_index("ix_tournaments_owner_created", table_name="tournaments")
    op.drop_index("ix_tournaments_status_created", table_name="tournaments")
    op.drop_table("tournaments")

"""Add competitive league seasons MVP tables, round-robin fixtures, and audit events.

Revision ID: c9f3e1a5d7b2
Revises: b8e2f9d4c6a1
"""

from alembic import op
import sqlalchemy as sa


revision = "c9f3e1a5d7b2"
down_revision = "b8e2f9d4c6a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. leagues table
    op.create_table(
        "leagues",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_player_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("mode", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("rules_version", sa.String(length=32), server_default="v1.0", nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 80", name="ck_league_name_length"),
        sa.CheckConstraint("mode IN ('classic', 'speed_challenge', 'elimination', 'power_play')", name="ck_league_mode"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_league_status"),
        sa.ForeignKeyConstraint(["owner_player_id"], ["player_profiles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_leagues_status_created", "leagues", ["status", "created_at"], unique=False)
    op.create_index("ix_leagues_owner_created", "leagues", ["owner_player_id", "created_at"], unique=False)

    # 2. league_seasons table
    op.create_table(
        "league_seasons",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("league_id", sa.String(length=32), nullable=False),
        sa.Column("season_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
        sa.Column("capacity", sa.Integer(), server_default="16", nullable=False),
        sa.Column("registration_opens_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registration_closes_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rules_version", sa.String(length=32), server_default="v1.0", nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 80", name="ck_season_name_length"),
        sa.CheckConstraint("season_number >= 1", name="ck_season_number"),
        sa.CheckConstraint("status IN ('draft', 'registration_open', 'registration_closed', 'in_progress', 'completed', 'cancelled')", name="ck_season_status"),
        sa.CheckConstraint("capacity BETWEEN 2 AND 32", name="ck_season_capacity"),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("league_id", "season_number", name="uq_league_season_number"),
    )
    op.create_index("ix_league_seasons_status_created", "league_seasons", ["status", "created_at"], unique=False)
    op.create_index("ix_league_seasons_league_num", "league_seasons", ["league_id", "season_number"], unique=False)

    # 3. season_participants table
    op.create_table(
        "season_participants",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("season_id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="registered", nullable=False),
        sa.Column("final_placement", sa.Integer(), nullable=True),
        sa.CheckConstraint("seed IS NULL OR seed >= 1", name="ck_season_participant_seed"),
        sa.CheckConstraint("final_placement IS NULL OR final_placement >= 1", name="ck_season_participant_placement"),
        sa.CheckConstraint("status IN ('registered', 'withdrawn', 'active', 'completed', 'disqualified')", name="ck_season_participant_status"),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["season_id"], ["league_seasons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season_id", "player_id", name="uq_season_participant_player"),
    )
    op.create_index("ix_season_participants_season_status", "season_participants", ["season_id", "status"], unique=False)

    # 4. season_rounds table
    op.create_table(
        "season_rounds",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("season_id", sa.String(length=32), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("round_number >= 1", name="ck_season_round_number"),
        sa.CheckConstraint("status IN ('pending', 'in_progress', 'completed')", name="ck_season_round_status"),
        sa.ForeignKeyConstraint(["season_id"], ["league_seasons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season_id", "round_number", name="uq_season_round_number"),
    )
    op.create_index("ix_season_rounds_season_num", "season_rounds", ["season_id", "round_number"], unique=False)

    # 5. season_fixtures table
    op.create_table(
        "season_fixtures",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("season_id", sa.String(length=32), nullable=False),
        sa.Column("round_id", sa.String(length=32), nullable=False),
        sa.Column("match_number", sa.Integer(), nullable=False),
        sa.Column("player_one_id", sa.String(length=32), nullable=True),
        sa.Column("player_two_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("official_match_id", sa.String(length=32), nullable=True),
        sa.Column("winner_player_id", sa.String(length=32), nullable=True),
        sa.Column("is_draw", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("player_one_score", sa.Integer(), nullable=True),
        sa.Column("player_two_score", sa.Integer(), nullable=True),
        sa.Column("result_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("match_number >= 1", name="ck_season_fixture_match_number"),
        sa.CheckConstraint("status IN ('pending', 'scheduled', 'in_progress', 'completed', 'bye', 'forfeited', 'disputed', 'cancelled')", name="ck_season_fixture_status"),
        sa.ForeignKeyConstraint(["official_match_id"], ["competitive_matches.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["player_one_id"], ["player_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["player_two_id"], ["player_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["round_id"], ["season_rounds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["season_id"], ["league_seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["winner_player_id"], ["player_profiles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("round_id", "match_number", name="uq_season_round_match"),
    )
    op.create_index("ix_season_fixtures_season_round", "season_fixtures", ["season_id", "round_id"], unique=False)
    op.create_index("ix_season_fixtures_official", "season_fixtures", ["official_match_id"], unique=False)

    # 6. season_audit_events table
    op.create_table(
        "season_audit_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("season_id", sa.String(length=32), nullable=False),
        sa.Column("actor_player_id", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("structured_summary", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["actor_player_id"], ["player_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["season_id"], ["league_seasons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_season_audit_season_time", "season_audit_events", ["season_id", "timestamp"], unique=False)

    # 7. season_fixture_tickets table
    op.create_table(
        "season_fixture_tickets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("season_id", sa.String(length=32), nullable=False),
        sa.Column("fixture_id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["season_id"], ["league_seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["fixture_id"], ["season_fixtures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_season_fixture_ticket_hash"),
    )
    op.create_index("ix_season_tickets_fixture_player", "season_fixture_tickets", ["fixture_id", "player_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_season_tickets_fixture_player", table_name="season_fixture_tickets")
    op.drop_table("season_fixture_tickets")
    op.drop_index("ix_season_audit_season_time", table_name="season_audit_events")
    op.drop_table("season_audit_events")
    op.drop_index("ix_season_fixtures_official", table_name="season_fixtures")
    op.drop_index("ix_season_fixtures_season_round", table_name="season_fixtures")
    op.drop_table("season_fixtures")
    op.drop_index("ix_season_rounds_season_num", table_name="season_rounds")
    op.drop_table("season_rounds")
    op.drop_index("ix_season_participants_season_status", table_name="season_participants")
    op.drop_table("season_participants")
    op.drop_index("ix_league_seasons_league_num", table_name="league_seasons")
    op.drop_index("ix_league_seasons_status_created", table_name="league_seasons")
    op.drop_table("league_seasons")
    op.drop_index("ix_leagues_owner_created", table_name="leagues")
    op.drop_index("ix_leagues_status_created", table_name="leagues")
    op.drop_table("leagues")

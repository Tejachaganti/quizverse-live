"""Add pseudonymous player profiles and immutable match history.

Revision ID: 8f4a7c2d1b60
Revises: 4e8fc5a1c220
"""

from alembic import op
import sqlalchemy as sa


revision = "8f4a7c2d1b60"
down_revision = "4e8fc5a1c220"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_profiles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(trim(display_name)) BETWEEN 1 AND 24", name="ck_player_display_name_length"),
        sa.CheckConstraint("status IN ('active', 'disabled', 'anonymized')", name="ck_player_status"),
        sa.CheckConstraint("version >= 1", name="ck_player_version"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_player_profiles_status_created", "player_profiles", ["status", "created_at"], unique=False)
    op.create_table(
        "player_credentials",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_player_credentials_token_hash"),
    )
    op.create_index("ix_player_credentials_player_active", "player_credentials", ["player_id", "revoked_at"], unique=False)
    op.create_table(
        "competitive_matches",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("engine_match_id", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="completed", nullable=False),
        sa.Column("result_kind", sa.String(length=16), nullable=False),
        sa.Column("rules_version", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("question_set_hash", sa.String(length=64), nullable=False),
        sa.Column("result_digest", sa.String(length=64), nullable=False),
        sa.Column("result_context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status = 'completed'", name="ck_competitive_match_status"),
        sa.CheckConstraint("mode IN ('classic', 'speed_challenge', 'team_battle', 'elimination', 'solo_practice', 'quiz_royale', 'power_play', 'bluff_master', 'escape_room')", name="ck_competitive_match_mode"),
        sa.CheckConstraint("result_kind IN ('individual', 'team', 'cooperative', 'practice')", name="ck_match_result_kind"),
        sa.CheckConstraint("length(question_set_hash) = 64", name="ck_match_question_hash_length"),
        sa.CheckConstraint("length(result_digest) = 64", name="ck_match_result_digest_length"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("engine_match_id", name="uq_competitive_matches_engine_match_id"),
    )
    op.create_index("ix_competitive_matches_completed_mode", "competitive_matches", ["completed_at", "mode"], unique=False)
    op.create_table(
        "match_participants",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("match_id", sa.String(length=32), nullable=False),
        sa.Column("player_id", sa.String(length=32), nullable=True),
        sa.Column("seat_index", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.String(length=24), nullable=True),
        sa.Column("final_score", sa.Integer(), nullable=True),
        sa.Column("correct_count", sa.Integer(), nullable=True),
        sa.Column("answer_count", sa.Integer(), nullable=True),
        sa.Column("placement", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("disconnect_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reconnect_expired", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("forfeited", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint("seat_index >= 0", name="ck_match_seat_index"),
        sa.CheckConstraint("final_score IS NULL OR final_score >= 0", name="ck_match_final_score"),
        sa.CheckConstraint("correct_count IS NULL OR correct_count >= 0", name="ck_match_correct_count"),
        sa.CheckConstraint("answer_count IS NULL OR answer_count >= 0", name="ck_match_answer_count"),
        sa.CheckConstraint("placement IS NULL OR placement >= 1", name="ck_match_placement"),
        sa.CheckConstraint("disconnect_count >= 0", name="ck_match_disconnect_count"),
        sa.CheckConstraint("outcome IN ('win', 'loss', 'draw', 'team_win', 'team_loss', 'team_draw', 'practice', 'cooperative_success', 'cooperative_failure', 'participated')", name="ck_match_participant_outcome"),
        sa.ForeignKeyConstraint(["match_id"], ["competitive_matches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["player_id"], ["player_profiles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "seat_index", name="uq_match_participant_seat"),
        sa.UniqueConstraint("match_id", "player_id", name="uq_match_participant_player"),
    )
    op.create_index("ix_match_participants_player_match", "match_participants", ["player_id", "match_id"], unique=False)
    op.create_table(
        "match_teams",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("match_id", sa.String(length=32), nullable=False),
        sa.Column("team_id", sa.String(length=24), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("correct_count", sa.Integer(), nullable=False),
        sa.Column("response_time_ms", sa.Integer(), nullable=False),
        sa.Column("placement", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=8), nullable=False),
        sa.CheckConstraint("placement IS NULL OR placement >= 1", name="ck_match_team_placement"),
        sa.CheckConstraint("score >= 0 AND correct_count >= 0 AND response_time_ms >= 0", name="ck_match_team_metrics"),
        sa.CheckConstraint("outcome IN ('win', 'loss', 'draw')", name="ck_match_team_outcome"),
        sa.ForeignKeyConstraint(["match_id"], ["competitive_matches.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "team_id", name="uq_match_team"),
    )


def downgrade() -> None:
    op.drop_table("match_teams")
    op.drop_index("ix_match_participants_player_match", table_name="match_participants")
    op.drop_table("match_participants")
    op.drop_index("ix_competitive_matches_completed_mode", table_name="competitive_matches")
    op.drop_table("competitive_matches")
    op.drop_index("ix_player_credentials_player_active", table_name="player_credentials")
    op.drop_table("player_credentials")
    op.drop_index("ix_player_profiles_status_created", table_name="player_profiles")
    op.drop_table("player_profiles")

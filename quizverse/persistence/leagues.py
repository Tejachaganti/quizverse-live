"""Competitive League Seasons MVP service: lifecycle, registration, round-robin fixtures, match integration, standings, and audit."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    CompetitiveMatch,
    League,
    LeagueSeason,
    PlayerProfile,
    PlayerRating,
    SeasonAuditEvent,
    SeasonFixture,
    SeasonFixtureTicket,
    SeasonParticipant,
    SeasonRound,
    utc_now,
)


LEAGUE_MODES = {"classic", "speed_challenge", "elimination", "power_play"}

ALLOWED_LEAGUE_TRANSITIONS = {
    "active": {"archived"},
    "archived": set(),
}

ALLOWED_SEASON_TRANSITIONS = {
    "draft": {"registration_open", "cancelled"},
    "registration_open": {"registration_closed", "in_progress", "cancelled"},
    "registration_closed": {"in_progress", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", errors="strict")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def generate_round_robin_schedule(player_ids: list[str]) -> list[list[tuple[str | None, str | None]]]:
    """Generates standard round-robin pairings using the circle method.

    If the number of players is odd, a dummy BYE player (None) is added.
    Every player plays every other player exactly once.
    If odd, each player receives at most one BYE.
    """
    n = len(player_ids)
    if n < 2:
        raise ValueError("At least 2 players are required for a round-robin schedule.")

    pool = list(player_ids)
    if n % 2 == 1:
        pool.append(None)

    total_slots = len(pool)
    rounds_count = total_slots - 1
    schedule: list[list[tuple[str | None, str | None]]] = []

    for round_idx in range(rounds_count):
        round_pairings: list[tuple[str | None, str | None]] = []
        for i in range(total_slots // 2):
            p1 = pool[i]
            p2 = pool[total_slots - 1 - i]
            if p1 is None:
                round_pairings.append((p2, None))
            elif p2 is None:
                round_pairings.append((p1, None))
            else:
                if (i + round_idx) % 2 == 1:
                    round_pairings.append((p2, p1))
                else:
                    round_pairings.append((p1, p2))
        schedule.append(round_pairings)
        # Rotate all elements except pool[0]
        pool = [pool[0]] + [pool[-1]] + pool[1:-1]

    return schedule


class LeagueError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class LeagueService:
    """Domain service for competitive leagues, seasons, round-robin fixtures, and standings."""

    def __init__(self, database: Database, engine: Any | None = None) -> None:
        self._database = database
        self._engine = engine
        self._fixture_rooms: dict[str, str] = {}  # fixture_id -> engine_room_code

    @property
    def engine(self) -> Any | None:
        return self._engine

    @engine.setter
    def engine(self, engine: Any | None) -> None:
        self._engine = engine

    # -------------------------------------------------------------------------
    # League Management
    # -------------------------------------------------------------------------

    def create_league(
        self,
        owner_player_id: str | None = None,
        name: str = "",
        mode: str = "classic",
        description: str | None = None,
        rules_version: str = "v1.0",
        configuration: dict[str, Any] | None = None,
        *,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        actual_owner_id = owner_player_id or actor_id
        if not actual_owner_id:
            raise LeagueError("owner_not_found", "Active owner player profile is required.", 404)
        if mode not in LEAGUE_MODES:
            raise LeagueError("invalid_league_mode", f"Mode '{mode}' is not supported for leagues.", 422)
        clean_name = name.strip()
        if not 1 <= len(clean_name) <= 80:
            raise LeagueError("invalid_league_name", "League name must be between 1 and 80 characters.", 422)

        league_id = secrets.token_hex(16)
        now = utc_now()
        config = configuration or {}

        with self._database.sessions.begin() as session:
            owner = session.get(PlayerProfile, actual_owner_id)
            if owner is None or owner.status != "active":
                raise LeagueError("owner_not_found", "Active owner player profile is required.", 404)

            league = League(
                id=league_id,
                owner_player_id=actual_owner_id,
                name=clean_name,
                description=description.strip() if description else None,
                mode=mode,
                status="active",
                rules_version=rules_version,
                configuration=config,
                created_at=now,
                updated_at=now,
            )
            session.add(league)

        return self.get_league(league_id)

    def update_league(
        self,
        league_id: str,
        actor_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        owner_player_id: str | None = None,
    ) -> dict[str, Any]:
        actual_actor_id = actor_id or owner_player_id
        if not actual_actor_id:
            raise LeagueError("unauthorized", "Authentication is required.", 401)

        with self._database.sessions.begin() as session:
            league = session.get(League, league_id)
            if league is None:
                raise LeagueError("league_not_found", "League not found.", 404)
            if league.owner_player_id != actual_actor_id:
                raise LeagueError("forbidden", "Only the league owner can modify the league.", 403)

            if name is not None:
                clean_name = name.strip()
                if not 1 <= len(clean_name) <= 80:
                    raise LeagueError("invalid_league_name", "League name must be between 1 and 80 characters.", 422)
                league.name = clean_name
            if description is not None:
                league.description = description.strip() if description else None

            league.updated_at = utc_now()

        return self.get_league(league_id)

    def get_league(self, league_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            league = session.scalar(
                select(League)
                .options(selectinload(League.owner), selectinload(League.seasons))
                .where(League.id == league_id)
            )
            if league is None:
                raise LeagueError("league_not_found", "League not found.", 404)

            seasons_summary = [
                {
                    "id": s.id,
                    "season_number": s.season_number,
                    "name": s.name,
                    "status": s.status,
                    "capacity": s.capacity,
                    "starts_at": s.starts_at.isoformat() if s.starts_at else None,
                    "ends_at": s.ends_at.isoformat() if s.ends_at else None,
                    "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in sorted(league.seasons, key=lambda item: item.season_number)
            ]

            return {
                "id": league.id,
                "owner_player_id": league.owner_player_id,
                "owner_display_name": league.owner.display_name if league.owner else "Unknown",
                "name": league.name,
                "description": league.description,
                "mode": league.mode,
                "status": league.status,
                "rules_version": league.rules_version,
                "configuration": league.configuration,
                "created_at": league.created_at.isoformat() if league.created_at else None,
                "updated_at": league.updated_at.isoformat() if league.updated_at else None,
                "seasons": seasons_summary,
                "seasons_count": len(seasons_summary),
            }

    def list_leagues(
        self,
        mode: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        with self._database.session() as session:
            query = select(League).options(selectinload(League.owner), selectinload(League.seasons))
            if mode:
                query = query.where(League.mode == mode)
            if status:
                query = query.where(League.status == status)

            count_query = select(func.count(League.id))
            if mode:
                count_query = count_query.where(League.mode == mode)
            if status:
                count_query = count_query.where(League.status == status)
            total = session.scalar(count_query) or 0

            leagues = session.scalars(
                query.order_by(League.created_at.desc()).limit(limit).offset(offset)
            ).all()

            items = []
            for l in leagues:
                items.append({
                    "id": l.id,
                    "owner_player_id": l.owner_player_id,
                    "owner_display_name": l.owner.display_name if l.owner else "Unknown",
                    "name": l.name,
                    "description": l.description,
                    "mode": l.mode,
                    "status": l.status,
                    "rules_version": l.rules_version,
                    "created_at": l.created_at.isoformat() if l.created_at else None,
                    "seasons_count": len(l.seasons),
                })

            return {"total": total, "limit": limit, "offset": offset, "leagues": items}

    # -------------------------------------------------------------------------
    # Season Management
    # -------------------------------------------------------------------------

    def create_season(
        self,
        league_id: str,
        actor_id: str | None = None,
        name: str = "",
        capacity: int = 16,
        registration_opens_at: datetime | None = None,
        registration_closes_at: datetime | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        rules_version: str = "v1.0",
        configuration: dict[str, Any] | None = None,
        *,
        owner_player_id: str | None = None,
    ) -> dict[str, Any]:
        actual_actor_id = actor_id or owner_player_id
        if not actual_actor_id:
            raise LeagueError("unauthorized", "Authentication is required.", 401)
        if not 2 <= capacity <= 32:
            raise LeagueError("invalid_capacity", "Season capacity must be between 2 and 32 players.", 422)
        clean_name = name.strip()
        if not 1 <= len(clean_name) <= 80:
            raise LeagueError("invalid_season_name", "Season name must be between 1 and 80 characters.", 422)

        season_id = secrets.token_hex(16)
        now = utc_now()
        config = configuration or {}

        with self._database.sessions.begin() as session:
            league = session.get(League, league_id)
            if league is None:
                raise LeagueError("league_not_found", "League not found.", 404)
            if league.owner_player_id != actual_actor_id:
                raise LeagueError("forbidden", "Only the league owner can create seasons.", 403)
            if league.status != "active":
                raise LeagueError("league_archived", "Cannot create seasons in an archived league.", 409)

            max_num = session.scalar(
                select(func.max(LeagueSeason.season_number)).where(LeagueSeason.league_id == league_id)
            ) or 0
            season_number = max_num + 1

            season = LeagueSeason(
                id=season_id,
                league_id=league_id,
                season_number=season_number,
                name=clean_name,
                status="draft",
                capacity=capacity,
                registration_opens_at=registration_opens_at,
                registration_closes_at=registration_closes_at,
                starts_at=starts_at,
                ends_at=ends_at,
                rules_version=rules_version,
                configuration=config,
                created_at=now,
                updated_at=now,
            )
            session.add(season)
            self._record_audit_event(
                session, season_id, actual_actor_id, "season_created", "Season created in draft state",
                {"name": clean_name, "season_number": season_number, "capacity": capacity},
            )

        return self.get_season(season_id)

    def transition_season_state(
        self,
        season_id: str,
        actor_id: str,
        new_status: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        with self._database.sessions.begin() as session:
            season = session.scalar(
                select(LeagueSeason).options(selectinload(LeagueSeason.league)).where(LeagueSeason.id == season_id)
            )
            if season is None:
                raise LeagueError("season_not_found", "Season not found.", 404)
            if season.league.owner_player_id != actor_id:
                raise LeagueError("forbidden", "Only the league owner can change season status.", 403)

            allowed = ALLOWED_SEASON_TRANSITIONS.get(season.status, set())
            if new_status not in allowed:
                raise LeagueError(
                    "invalid_transition",
                    f"Cannot transition season from '{season.status}' to '{new_status}'.",
                    409,
                )

            old_status = season.status
            season.status = new_status
            season.updated_at = utc_now()
            if new_status == "in_progress" and season.starts_at is None:
                season.starts_at = utc_now()
            elif new_status == "completed" and season.completed_at is None:
                season.completed_at = utc_now()

            self._record_audit_event(
                session, season_id, actor_id, f"status_{new_status}", reason,
                {"from_status": old_status, "to_status": new_status},
            )

        return self.get_season(season_id)

    def open_registration(self, season_id: str, actor_id: str | None = None, owner_player_id: str | None = None) -> dict[str, Any]:
        return self.transition_season_state(season_id, actor_id or owner_player_id or "", "registration_open", "Owner opened registration")

    def close_registration(self, season_id: str, actor_id: str | None = None, owner_player_id: str | None = None) -> dict[str, Any]:
        return self.transition_season_state(season_id, actor_id or owner_player_id or "", "registration_closed", "Owner closed registration")

    def cancel_season(self, season_id: str, actor_id: str | None = None, reason: str = "Cancelled by owner", owner_player_id: str | None = None) -> dict[str, Any]:
        return self.transition_season_state(season_id, actor_id or owner_player_id or "", "cancelled", reason)

    # -------------------------------------------------------------------------
    # Participant Registration
    # -------------------------------------------------------------------------

    def register_player(self, season_id: str, player_id: str) -> dict[str, Any]:
        now = utc_now()
        with self._database.sessions.begin() as session:
            season = session.get(LeagueSeason, season_id)
            if season is None:
                raise LeagueError("season_not_found", "Season not found.", 404)
            if season.status != "registration_open":
                raise LeagueError("registration_closed", f"Season registration is not open (status: {season.status}).", 409)

            player = session.get(PlayerProfile, player_id)
            if player is None or player.status != "active":
                raise LeagueError("player_not_found", "Active player profile required to register.", 404)

            existing = session.scalar(
                select(SeasonParticipant).where(
                    SeasonParticipant.season_id == season_id,
                    SeasonParticipant.player_id == player_id,
                )
            )
            if existing:
                if existing.status == "withdrawn":
                    # Re-join
                    existing.status = "registered"
                    existing.registered_at = now
                    self._record_audit_event(
                        session, season_id, player_id, "player_re_registered", "Player re-registered after withdrawal",
                        {"player_id": player_id, "display_name": player.display_name},
                    )
                    return {"registered": True, "season_id": season_id, "player_id": player_id}
                raise LeagueError("already_registered", "Player is already registered for this season.", 409)

            active_count = session.scalar(
                select(func.count(SeasonParticipant.id)).where(
                    SeasonParticipant.season_id == season_id,
                    SeasonParticipant.status == "registered",
                )
            ) or 0
            if active_count >= season.capacity:
                raise LeagueError("season_full", f"Season has reached capacity limit of {season.capacity}.", 409)

            participant = SeasonParticipant(
                id=secrets.token_hex(16),
                season_id=season_id,
                player_id=player_id,
                registered_at=now,
                status="registered",
            )
            session.add(participant)
            self._record_audit_event(
                session, season_id, player_id, "player_registered", "Player registered for season",
                {"player_id": player_id, "display_name": player.display_name},
            )

        return {"registered": True, "season_id": season_id, "player_id": player_id}

    def withdraw_player(self, season_id: str, player_id: str) -> dict[str, Any]:
        with self._database.sessions.begin() as session:
            season = session.get(LeagueSeason, season_id)
            if season is None:
                raise LeagueError("season_not_found", "Season not found.", 404)
            if season.status != "registration_open":
                raise LeagueError("cannot_withdraw", "Cannot withdraw after registration is closed.", 409)

            participant = session.scalar(
                select(SeasonParticipant).where(
                    SeasonParticipant.season_id == season_id,
                    SeasonParticipant.player_id == player_id,
                    SeasonParticipant.status == "registered",
                )
            )
            if participant is None:
                raise LeagueError("not_registered", "Player is not registered for this season.", 404)

            participant.status = "withdrawn"
            self._record_audit_event(
                session, season_id, player_id, "player_withdrawn", "Player withdrew from season",
                {"player_id": player_id},
            )

        return {"withdrawn": True, "season_id": season_id, "player_id": player_id}

    # -------------------------------------------------------------------------
    # Fixture Generation & Season Start
    # -------------------------------------------------------------------------

    def start_season(self, season_id: str, actor_id: str | None = None, *, owner_player_id: str | None = None) -> dict[str, Any]:
        actual_actor_id = actor_id or owner_player_id
        if not actual_actor_id:
            raise LeagueError("unauthorized", "Authentication is required.", 401)

        now = utc_now()
        with self._database.sessions.begin() as session:
            season = session.scalar(
                select(LeagueSeason)
                .options(selectinload(LeagueSeason.league))
                .where(LeagueSeason.id == season_id)
            )
            if season is None:
                raise LeagueError("season_not_found", "Season not found.", 404)
            if season.league.owner_player_id != actual_actor_id:
                raise LeagueError("forbidden", "Only the league owner can start the season.", 403)
            if season.status not in {"registration_open", "registration_closed"}:
                raise LeagueError(
                    "invalid_state",
                    f"Season must be in registration_open or registration_closed to start (current: {season.status}).",
                    409,
                )

            participants = session.scalars(
                select(SeasonParticipant)
                .where(SeasonParticipant.season_id == season_id, SeasonParticipant.status == "registered")
            ).all()

            count = len(participants)
            if count < 2:
                raise LeagueError("insufficient_players", "At least 2 registered players are required to start a season.", 409)

            league_mode = season.league.mode

            # Deterministic seeding: mode rating -> global rating -> registration timestamp -> player_id
            def seed_key(p: SeasonParticipant):
                mode_r = session.scalar(
                    select(PlayerRating.rating).where(
                        PlayerRating.player_id == p.player_id,
                        PlayerRating.rating_scope == f"mode:{league_mode}",
                    )
                ) or 1000
                glob_r = session.scalar(
                    select(PlayerRating.rating).where(
                        PlayerRating.player_id == p.player_id,
                        PlayerRating.rating_scope == "global",
                    )
                ) or 1000
                return (-mode_r, -glob_r, p.registered_at, p.player_id)

            sorted_participants = sorted(participants, key=seed_key)

            for seed_idx, p in enumerate(sorted_participants, start=1):
                p.seed = seed_idx
                p.status = "active"

            seeded_player_ids = [p.player_id for p in sorted_participants]
            rounds_schedule = generate_round_robin_schedule(seeded_player_ids)

            for round_num, pairings in enumerate(rounds_schedule, start=1):
                round_id = secrets.token_hex(16)
                s_round = SeasonRound(
                    id=round_id,
                    season_id=season_id,
                    round_number=round_num,
                    status="in_progress" if round_num == 1 else "pending",
                    scheduled_at=now,
                )
                session.add(s_round)

                for match_idx, (p1_id, p2_id) in enumerate(pairings, start=1):
                    fixture_id = secrets.token_hex(16)
                    if p2_id is None:
                        # BYE fixture
                        fixture = SeasonFixture(
                            id=fixture_id,
                            season_id=season_id,
                            round_id=round_id,
                            match_number=match_idx,
                            player_one_id=p1_id,
                            player_two_id=None,
                            status="bye",
                            result_reason="scheduled_bye",
                            created_at=now,
                            completed_at=now,
                        )
                    else:
                        fixture = SeasonFixture(
                            id=fixture_id,
                            season_id=season_id,
                            round_id=round_id,
                            match_number=match_idx,
                            player_one_id=p1_id,
                            player_two_id=p2_id,
                            status="scheduled",
                            scheduled_at=now,
                            created_at=now,
                        )
                    session.add(fixture)

            season.status = "in_progress"
            season.starts_at = now
            season.updated_at = now

            self._record_audit_event(
                session, season_id, actual_actor_id, "season_started", "Season started and round-robin fixtures generated",
                {
                    "participant_count": count,
                    "total_rounds": len(rounds_schedule),
                    "seeded_players": seeded_player_ids,
                },
            )

        return self.get_season(season_id)

    # -------------------------------------------------------------------------
    # Standings & Tie-breakers
    # -------------------------------------------------------------------------

    def get_standings(self, season_id: str) -> list[dict[str, Any]]:
        with self._database.session() as session:
            season = session.get(LeagueSeason, season_id)
            if season is None:
                raise LeagueError("season_not_found", "Season not found.", 404)

            participants = session.scalars(
                select(SeasonParticipant)
                .options(selectinload(SeasonParticipant.player))
                .where(SeasonParticipant.season_id == season_id)
            ).all()

            fixtures = session.scalars(
                select(SeasonFixture).where(SeasonFixture.season_id == season_id)
            ).all()

            return self._calculate_standings(participants, fixtures)

    @staticmethod
    def _calculate_standings(
        participants: Sequence[SeasonParticipant],
        fixtures: Sequence[SeasonFixture],
    ) -> list[dict[str, Any]]:
        """Calculates season standings with strict, deterministic tie-breakers:

        1. Total Points (Win: 3, Draw: 1, Loss: 0, Bye: 0)
        2. Head-to-head points among tied players (where applicable)
        3. Score difference (Score For - Score Against)
        4. Match wins count
        5. Stable player identifier (player_id ascending)
        """
        stats: dict[str, dict[str, Any]] = {}
        for p in participants:
            if p.status == "withdrawn":
                continue
            stats[p.player_id] = {
                "player_id": p.player_id,
                "display_name": p.player.display_name if p.player else p.player_id,
                "seed": p.seed,
                "matches_played": 0,
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "byes": 0,
                "points": 0,
                "score_for": 0,
                "score_against": 0,
                "score_diff": 0,
                "status": p.status,
                "final_placement": p.final_placement,
            }

        # Accumulate match results
        for f in fixtures:
            if f.status == "bye":
                if f.player_one_id and f.player_one_id in stats:
                    stats[f.player_one_id]["byes"] += 1
                continue

            if f.status in {"completed", "forfeited"}:
                p1_id = f.player_one_id
                p2_id = f.player_two_id
                if not p1_id or not p2_id or p1_id not in stats or p2_id not in stats:
                    continue

                s1 = f.player_one_score if f.player_one_score is not None else 0
                s2 = f.player_two_score if f.player_two_score is not None else 0

                stats[p1_id]["matches_played"] += 1
                stats[p2_id]["matches_played"] += 1
                stats[p1_id]["score_for"] += s1
                stats[p1_id]["score_against"] += s2
                stats[p2_id]["score_for"] += s2
                stats[p2_id]["score_against"] += s1

                if f.is_draw:
                    stats[p1_id]["draws"] += 1
                    stats[p1_id]["points"] += 1
                    stats[p2_id]["draws"] += 1
                    stats[p2_id]["points"] += 1
                elif f.winner_player_id == p1_id:
                    stats[p1_id]["wins"] += 1
                    stats[p1_id]["points"] += 3
                    stats[p2_id]["losses"] += 1
                elif f.winner_player_id == p2_id:
                    stats[p2_id]["wins"] += 1
                    stats[p2_id]["points"] += 3
                    stats[p1_id]["losses"] += 1

        for row in stats.values():
            row["score_diff"] = row["score_for"] - row["score_against"]

        # Group participants by points to compute Head-to-Head among tied players
        by_points: dict[int, list[str]] = {}
        for pid, row in stats.items():
            by_points.setdefault(row["points"], []).append(pid)

        h2h_points: dict[str, int] = {pid: 0 for pid in stats}
        for pts, tied_pids in by_points.items():
            if len(tied_pids) > 1:
                tied_set = set(tied_pids)
                for f in fixtures:
                    if f.status in {"completed", "forfeited"}:
                        if f.player_one_id in tied_set and f.player_two_id in tied_set:
                            if f.is_draw:
                                h2h_points[f.player_one_id] += 1
                                h2h_points[f.player_two_id] += 1
                            elif f.winner_player_id == f.player_one_id:
                                h2h_points[f.player_one_id] += 3
                            elif f.winner_player_id == f.player_two_id:
                                h2h_points[f.player_two_id] += 3

        for pid, row in stats.items():
            row["h2h_points"] = h2h_points[pid]

        def rank_sort_key(item: dict[str, Any]):
            return (
                -item["points"],
                -item["h2h_points"],
                -item["score_diff"],
                -item["wins"],
                item["player_id"],
            )

        sorted_standings = sorted(stats.values(), key=rank_sort_key)
        for idx, row in enumerate(sorted_standings, start=1):
            row["rank"] = idx

        return sorted_standings

    # -------------------------------------------------------------------------
    # Fixture Details & Schedule
    # -------------------------------------------------------------------------

    def get_fixtures(self, season_id: str, round_number: int | None = None) -> list[dict[str, Any]]:
        with self._database.session() as session:
            season = session.get(LeagueSeason, season_id)
            if season is None:
                raise LeagueError("season_not_found", "Season not found.", 404)

            query = (
                select(SeasonFixture)
                .options(
                    selectinload(SeasonFixture.player_one),
                    selectinload(SeasonFixture.player_two),
                    selectinload(SeasonFixture.winner),
                    selectinload(SeasonFixture.round),
                )
                .where(SeasonFixture.season_id == season_id)
            )
            if round_number is not None:
                query = query.join(SeasonRound).where(SeasonRound.round_number == round_number)

            fixtures = session.scalars(
                query.order_by(SeasonFixture.round_id, SeasonFixture.match_number)
            ).all()

            return [self._fixture_view(f) for f in fixtures]

    @staticmethod
    def _fixture_view(f: SeasonFixture) -> dict[str, Any]:
        return {
            "id": f.id,
            "season_id": f.season_id,
            "round_id": f.round_id,
            "round_number": f.round.round_number if f.round else None,
            "match_number": f.match_number,
            "player_one_id": f.player_one_id,
            "player_one_name": f.player_one.display_name if f.player_one else None,
            "player_two_id": f.player_two_id,
            "player_two_name": f.player_two.display_name if f.player_two else ("BYE" if f.status == "bye" else None),
            "status": f.status,
            "scheduled_at": f.scheduled_at.isoformat() if f.scheduled_at else None,
            "official_match_id": f.official_match_id,
            "winner_player_id": f.winner_player_id,
            "winner_name": f.winner.display_name if f.winner else None,
            "is_draw": f.is_draw,
            "player_one_score": f.player_one_score,
            "player_two_score": f.player_two_score,
            "result_reason": f.result_reason,
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "completed_at": f.completed_at.isoformat() if f.completed_at else None,
        }

    def get_season(self, season_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            season = session.scalar(
                select(LeagueSeason)
                .options(
                    selectinload(LeagueSeason.league),
                    selectinload(LeagueSeason.participants).selectinload(SeasonParticipant.player),
                    selectinload(LeagueSeason.rounds),
                    selectinload(LeagueSeason.fixtures).options(
                        selectinload(SeasonFixture.player_one),
                        selectinload(SeasonFixture.player_two),
                        selectinload(SeasonFixture.winner),
                        selectinload(SeasonFixture.round),
                    ),
                )
                .where(LeagueSeason.id == season_id)
            )
            if season is None:
                raise LeagueError("season_not_found", "Season not found.", 404)

            standings = self._calculate_standings(season.participants, season.fixtures)

            participants_view = [
                {
                    "id": p.id,
                    "player_id": p.player_id,
                    "display_name": p.player.display_name if p.player else p.player_id,
                    "seed": p.seed,
                    "status": p.status,
                    "final_placement": p.final_placement,
                    "registered_at": p.registered_at.isoformat() if p.registered_at else None,
                }
                for p in sorted(season.participants, key=lambda item: (item.seed or 999, item.registered_at))
            ]

            fixtures_by_round: dict[int, list[dict[str, Any]]] = {}
            for f in sorted(season.fixtures, key=lambda item: (item.round.round_number if item.round else 0, item.match_number)):
                r_num = f.round.round_number if f.round else 1
                fixtures_by_round.setdefault(r_num, []).append(self._fixture_view(f))

            rounds_view = [
                {
                    "id": r.id,
                    "round_number": r.round_number,
                    "status": r.status,
                    "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                    "fixtures": fixtures_by_round.get(r.round_number, []),
                }
                for r in sorted(season.rounds, key=lambda item: item.round_number)
            ]

            active_registered = sum(1 for p in season.participants if p.status in {"registered", "active", "completed"})

            return {
                "id": season.id,
                "league_id": season.league_id,
                "league_name": season.league.name if season.league else "Unknown",
                "league_mode": season.league.mode if season.league else "classic",
                "owner_player_id": season.league.owner_player_id if season.league else None,
                "season_number": season.season_number,
                "name": season.name,
                "status": season.status,
                "capacity": season.capacity,
                "registered_count": active_registered,
                "registration_opens_at": season.registration_opens_at.isoformat() if season.registration_opens_at else None,
                "registration_closes_at": season.registration_closes_at.isoformat() if season.registration_closes_at else None,
                "starts_at": season.starts_at.isoformat() if season.starts_at else None,
                "ends_at": season.ends_at.isoformat() if season.ends_at else None,
                "completed_at": season.completed_at.isoformat() if season.completed_at else None,
                "rules_version": season.rules_version,
                "configuration": season.configuration,
                "created_at": season.created_at.isoformat() if season.created_at else None,
                "updated_at": season.updated_at.isoformat() if season.updated_at else None,
                "participants": participants_view,
                "rounds": rounds_view,
                "standings": standings,
            }

    # -------------------------------------------------------------------------
    # Fixture Admission Tickets & Match Launch
    # -------------------------------------------------------------------------

    def create_fixture_ticket(
        self,
        season_id: str,
        fixture_id: str,
        player_id: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._database.sessions.begin() as session:
            season = session.get(LeagueSeason, season_id)
            fixture = session.get(SeasonFixture, fixture_id)
            if season is None or fixture is None or fixture.season_id != season_id:
                raise LeagueError("fixture_not_found", "Season fixture not found.", 404)

            if fixture.status not in {"scheduled", "ready", "in_progress"}:
                raise LeagueError("invalid_fixture_status", f"Fixture is not playable (status: {fixture.status}).", 409)

            if player_id not in {fixture.player_one_id, fixture.player_two_id}:
                raise LeagueError("forbidden", "Only assigned fixture participants can request admission tickets.", 403)

            token = secrets.token_urlsafe(32)
            ticket_hash = _token_hash(token)
            expires_at = now + timedelta(seconds=60)

            ticket = SeasonFixtureTicket(
                id=secrets.token_hex(16),
                season_id=season_id,
                fixture_id=fixture_id,
                player_id=player_id,
                token_hash=ticket_hash,
                expires_at=expires_at,
                created_at=now,
            )
            session.add(ticket)

        return {
            "ticket": token,
            "expires_at": expires_at.isoformat(),
            "fixture_id": fixture_id,
            "season_id": season_id,
            "player_id": player_id,
        }

    def validate_and_consume_ticket(
        self,
        season_id: str,
        fixture_id: str,
        player_id: str,
        token: str,
    ) -> SeasonFixtureTicket:
        now = utc_now()
        token_hash = _token_hash(token)
        with self._database.sessions.begin() as session:
            ticket = session.scalar(
                select(SeasonFixtureTicket).where(
                    SeasonFixtureTicket.token_hash == token_hash,
                    SeasonFixtureTicket.season_id == season_id,
                    SeasonFixtureTicket.fixture_id == fixture_id,
                    SeasonFixtureTicket.player_id == player_id,
                )
            )
            if ticket is None:
                raise LeagueError("invalid_ticket", "Fixture ticket is invalid or does not match this fixture.", 401)
            if ticket.used_at is not None:
                raise LeagueError("ticket_already_used", "Fixture ticket has already been consumed.", 409)
            if _as_utc(ticket.expires_at) < now:
                raise LeagueError("ticket_expired", "Fixture ticket has expired.", 401)

            ticket.used_at = now
            return ticket

    async def launch_fixture_match(
        self,
        engine: Any,
        season_id: str,
        fixture_id: str,
        player_id: str,
        player_name: str,
        token: str,
    ) -> dict[str, Any]:
        self.validate_and_consume_ticket(season_id, fixture_id, player_id, token)

        with self._database.session() as session:
            season = session.scalar(
                select(LeagueSeason).options(selectinload(LeagueSeason.league)).where(LeagueSeason.id == season_id)
            )
            fixture = session.get(SeasonFixture, fixture_id)
            if season is None or fixture is None:
                raise LeagueError("fixture_not_found", "Season fixture not found.", 404)
            mode = season.league.mode

        room_code = self._fixture_rooms.get(fixture_id)
        if room_code is None:
            frozen_selection = {
                "mode": mode,
                "question_count": 5,
                "league_id": season.league_id,
                "league_season_id": season_id,
                "league_fixture_id": fixture_id,
            }
            joined = await engine.create_room(
                player_name,
                mode=mode,
                selection=frozen_selection,
                profile_id=player_id,
            )
            self._fixture_rooms[fixture_id] = joined["state"]["code"]
            with self._database.sessions.begin() as session:
                f = session.get(SeasonFixture, fixture_id)
                if f and f.status == "scheduled":
                    f.status = "in_progress"
            return joined
        else:
            joined = await engine.join_room(room_code, player_name, profile_id=player_id)
            with self._database.sessions.begin() as session:
                f = session.get(SeasonFixture, fixture_id)
                if f and f.status == "scheduled":
                    f.status = "in_progress"
            return joined

    # -------------------------------------------------------------------------
    # Official Result Processing
    # -------------------------------------------------------------------------

    def process_fixture_completion(
        self,
        session,
        fixture_id: str,
        official_match_id: str,
        result_payload: dict[str, Any],
    ) -> bool:
        """Processes official server-authoritative fixture match completion idempotently."""
        fixture = session.get(SeasonFixture, fixture_id)
        if fixture is None:
            return False
        if fixture.status in {"completed", "forfeited"}:
            return False  # Idempotent: already processed

        participants = result_payload.get("participants", [])
        p_ids = {p.get("player_id") for p in participants if p.get("player_id")}
        if not {fixture.player_one_id, fixture.player_two_id}.issubset(p_ids):
            fixture.status = "disputed"
            fixture.result_reason = "participant_mismatch"
            return False

        # Extract scores and placements
        p1_res = next((p for p in participants if p.get("player_id") == fixture.player_one_id), None)
        p2_res = next((p for p in participants if p.get("player_id") == fixture.player_two_id), None)

        now = utc_now()
        fixture.official_match_id = official_match_id
        fixture.completed_at = now
        fixture.player_one_score = p1_res.get("final_score") if p1_res else 0
        fixture.player_two_score = p2_res.get("final_score") if p2_res else 0

        p1_place = p1_res.get("placement") if p1_res else None
        p2_place = p2_res.get("placement") if p2_res else None

        if p1_place == p2_place:
            fixture.is_draw = True
            fixture.winner_player_id = None
            fixture.status = "completed"
            fixture.result_reason = "draw"
        elif p1_place == 1:
            fixture.is_draw = False
            fixture.winner_player_id = fixture.player_one_id
            fixture.status = "completed"
            fixture.result_reason = "normal"
        elif p2_place == 1:
            fixture.is_draw = False
            fixture.winner_player_id = fixture.player_two_id
            fixture.status = "completed"
            fixture.result_reason = "normal"
        else:
            # Fallback comparison by score
            if (fixture.player_one_score or 0) > (fixture.player_two_score or 0):
                fixture.is_draw = False
                fixture.winner_player_id = fixture.player_one_id
                fixture.status = "completed"
                fixture.result_reason = "normal"
            elif (fixture.player_two_score or 0) > (fixture.player_one_score or 0):
                fixture.is_draw = False
                fixture.winner_player_id = fixture.player_two_id
                fixture.status = "completed"
                fixture.result_reason = "normal"
            else:
                fixture.is_draw = True
                fixture.winner_player_id = None
                fixture.status = "completed"
                fixture.result_reason = "draw"

        self._record_audit_event(
            session, fixture.season_id, None, "fixture_completed", "Fixture match officially completed",
            {
                "fixture_id": fixture_id,
                "official_match_id": official_match_id,
                "winner_player_id": fixture.winner_player_id,
                "is_draw": fixture.is_draw,
                "player_one_score": fixture.player_one_score,
                "player_two_score": fixture.player_two_score,
            },
        )

        # Check round and season completion
        self._check_and_update_season_completion(session, fixture.season_id)
        return True

    def forfeit_fixture(
        self,
        season_id: str,
        fixture_id: str,
        actor_id: str,
        reason: str = "player_forfeit",
    ) -> dict[str, Any]:
        now = utc_now()
        with self._database.sessions.begin() as session:
            season = session.scalar(
                select(LeagueSeason).options(selectinload(LeagueSeason.league)).where(LeagueSeason.id == season_id)
            )
            fixture = session.get(SeasonFixture, fixture_id)
            if season is None or fixture is None or fixture.season_id != season_id:
                raise LeagueError("fixture_not_found", "Fixture not found.", 404)

            is_p1 = actor_id == fixture.player_one_id
            is_p2 = actor_id == fixture.player_two_id
            is_owner = actor_id == season.league.owner_player_id

            if not (is_p1 or is_p2 or is_owner):
                raise LeagueError("forbidden", "Only fixture participants or the league owner can forfeit a match.", 403)

            if fixture.status in {"completed", "forfeited", "cancelled", "bye"}:
                raise LeagueError("invalid_fixture_status", f"Fixture is already finalized ({fixture.status}).", 409)

            if is_p1:
                winner_id = fixture.player_two_id
                fixture.player_one_score = 0
                fixture.player_two_score = 1
            elif is_p2:
                winner_id = fixture.player_one_id
                fixture.player_one_score = 1
                fixture.player_two_score = 0
            else:
                # Owner forfeit: defaults to awarding to player_one
                winner_id = fixture.player_one_id
                fixture.player_one_score = 1
                fixture.player_two_score = 0

            fixture.status = "forfeited"
            fixture.winner_player_id = winner_id
            fixture.is_draw = False
            fixture.result_reason = reason
            fixture.completed_at = now

            self._record_audit_event(
                session, season_id, actor_id, "fixture_forfeited", f"Fixture forfeited: {reason}",
                {"fixture_id": fixture_id, "forfeiting_actor": actor_id, "winner_player_id": winner_id},
            )

            self._check_and_update_season_completion(session, season_id)

        return self.get_season(season_id)

    def dispute_fixture(
        self,
        season_id: str,
        fixture_id: str,
        actor_id: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._database.sessions.begin() as session:
            season = session.scalar(
                select(LeagueSeason).options(selectinload(LeagueSeason.league)).where(LeagueSeason.id == season_id)
            )
            fixture = session.get(SeasonFixture, fixture_id)
            if season is None or fixture is None or fixture.season_id != season_id:
                raise LeagueError("fixture_not_found", "Fixture not found.", 404)

            if actor_id not in {fixture.player_one_id, fixture.player_two_id, season.league.owner_player_id}:
                raise LeagueError("forbidden", "Only fixture participants or the league owner can dispute a fixture.", 403)

            if fixture.status not in {"completed", "forfeited"}:
                fixture.status = "disputed"
            fixture.result_reason = reason

            self._record_audit_event(
                session, season_id, actor_id, "fixture_disputed", reason,
                {"fixture_id": fixture_id, "details": details or {}},
            )

        return self.get_season(season_id)

    def _check_and_update_season_completion(self, session, season_id: str) -> None:
        """Checks if all fixtures in a season are finished; if so, crowns standings and completes season."""
        season = session.get(LeagueSeason, season_id)
        if season is None or season.status == "completed":
            return

        all_fixtures = session.scalars(
            select(SeasonFixture).where(SeasonFixture.season_id == season_id)
        ).all()

        if not all_fixtures:
            return

        non_terminal = [f for f in all_fixtures if f.status not in {"completed", "forfeited", "bye", "cancelled"}]
        if not non_terminal:
            # All fixtures are terminal: finalize season
            participants = session.scalars(
                select(SeasonParticipant)
                .options(selectinload(SeasonParticipant.player))
                .where(SeasonParticipant.season_id == season_id)
            ).all()

            standings = self._calculate_standings(participants, all_fixtures)
            placement_map = {row["player_id"]: row["rank"] for row in standings}

            for p in participants:
                if p.player_id in placement_map:
                    p.final_placement = placement_map[p.player_id]
                    p.status = "completed"

            now = utc_now()
            season.status = "completed"
            season.completed_at = now
            season.updated_at = now

            champion_id = standings[0]["player_id"] if standings else None
            champion_name = standings[0]["display_name"] if standings else None

            self._record_audit_event(
                session, season_id, None, "season_completed", "Season completed and standings finalized",
                {
                    "champion_player_id": champion_id,
                    "champion_name": champion_name,
                    "final_standings": [
                        {"rank": s["rank"], "player_id": s["player_id"], "points": s["points"]}
                        for s in standings[:5]
                    ],
                },
            )

    # -------------------------------------------------------------------------
    # Audit Trail
    # -------------------------------------------------------------------------

    def get_audit_events(self, season_id: str) -> list[dict[str, Any]]:
        with self._database.session() as session:
            season = session.get(LeagueSeason, season_id)
            if season is None:
                raise LeagueError("season_not_found", "Season not found.", 404)

            events = session.scalars(
                select(SeasonAuditEvent)
                .options(selectinload(SeasonAuditEvent.actor))
                .where(SeasonAuditEvent.season_id == season_id)
                .order_by(SeasonAuditEvent.timestamp.asc())
            ).all()

            return [
                {
                    "id": e.id,
                    "season_id": e.season_id,
                    "actor_player_id": e.actor_player_id,
                    "actor_display_name": e.actor.display_name if e.actor else "System",
                    "action": e.action,
                    "reason": e.reason,
                    "timestamp": e.timestamp.isoformat(),
                    "structured_summary": e.structured_summary,
                }
                for e in events
            ]

    @staticmethod
    def _record_audit_event(
        session,
        season_id: str,
        actor_player_id: str | None,
        action: str,
        reason: str | None,
        structured_summary: dict[str, Any],
    ) -> None:
        event = SeasonAuditEvent(
            id=secrets.token_hex(16),
            season_id=season_id,
            actor_player_id=actor_player_id,
            action=action,
            reason=reason,
            timestamp=utc_now(),
            structured_summary=structured_summary,
        )
        session.add(event)

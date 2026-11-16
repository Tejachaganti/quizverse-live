"""Tournament MVP service: lifecycle, registration, single-elimination brackets, match integration, and audit."""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    CompetitiveMatch,
    PlayerProfile,
    PlayerRating,
    Tournament,
    TournamentAuditEvent,
    TournamentFixtureTicket,
    TournamentMatch,
    TournamentParticipant,
    TournamentRound,
    utc_now,
)


TOURNAMENT_MODES = {"classic", "speed_challenge", "elimination", "power_play"}

ALLOWED_TRANSITIONS = {
    "draft": {"registration_open", "cancelled"},
    "registration_open": {"registration_closed", "in_progress", "cancelled"},
    "registration_closed": {"scheduled", "in_progress", "cancelled"},
    "scheduled": {"in_progress", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", errors="strict")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def get_bracket_slots(size: int) -> list[int]:
    """Generates standard tournament bracket seed pairings for a power of two."""
    slots = [1, 2]
    while len(slots) < size:
        next_size = len(slots) * 2
        next_slots: list[int] = []
        for s in slots:
            next_slots.append(s)
            next_slots.append(next_size + 1 - s)
        slots = next_slots
    return slots


class TournamentError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class TournamentService:
    """Core domain service for tournament creation, registration, brackets, and match orchestration."""

    def __init__(self, database: Database, engine: Any | None = None, notification_service: Any = None) -> None:
        self._database = database
        self._engine = engine
        self.notification_service = notification_service
        self._match_rooms: dict[str, str] = {}  # tournament_match_id -> engine_room_code

    @property
    def engine(self) -> Any | None:
        return self._engine

    @engine.setter
    def engine(self, engine: Any | None) -> None:
        self._engine = engine

    # -------------------------------------------------------------------------
    # Lifecycle Management
    # -------------------------------------------------------------------------

    def create_tournament(
        self,
        owner_player_id: str | None = None,
        name: str = "",
        mode: str = "classic",
        capacity: int = 8,
        description: str | None = None,
        registration_opens_at: datetime | None = None,
        registration_closes_at: datetime | None = None,
        scheduled_start_at: datetime | None = None,
        rules_version: str = "v1.0",
        configuration: dict[str, Any] | None = None,
        *,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        actual_owner_id = owner_player_id or owner_id
        if not actual_owner_id:
            raise TournamentError("owner_not_found", "Active owner player profile is required.", 404)
        if mode not in TOURNAMENT_MODES:
            raise TournamentError("invalid_tournament_mode", f"Mode '{mode}' is not supported for tournaments.", 422)
        if not 2 <= capacity <= 64:
            raise TournamentError("invalid_capacity", "Capacity must be between 2 and 64 players.", 422)
        clean_name = name.strip()
        if not 1 <= len(clean_name) <= 80:
            raise TournamentError("invalid_tournament_name", "Tournament name must be between 1 and 80 characters.", 422)

        tournament_id = secrets.token_hex(16)
        now = utc_now()
        config = configuration or {}

        with self._database.sessions.begin() as session:
            owner = session.get(PlayerProfile, actual_owner_id)
            if owner is None or owner.status != "active":
                raise TournamentError("owner_not_found", "Active owner player profile is required.", 404)

            tournament = Tournament(
                id=tournament_id,
                owner_player_id=actual_owner_id,
                name=clean_name,
                description=description.strip() if description else None,
                format="single_elimination",
                mode=mode,
                status="draft",
                capacity=capacity,
                registration_opens_at=registration_opens_at,
                registration_closes_at=registration_closes_at,
                scheduled_start_at=scheduled_start_at,
                rules_version=rules_version,
                configuration=config,
                created_at=now,
                updated_at=now,
            )
            session.add(tournament)
            self._record_audit_event(
                session, tournament_id, actual_owner_id, "tournament_created", "Tournament created in draft state",
                {"name": clean_name, "mode": mode, "capacity": capacity},
            )

        return self.get_tournament(tournament_id)

    def update_tournament(
        self,
        tournament_id: str,
        actor_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        capacity: int | None = None,
        registration_opens_at: datetime | None = None,
        registration_closes_at: datetime | None = None,
        scheduled_start_at: datetime | None = None,
        configuration: dict[str, Any] | None = None,
        owner_player_id: str | None = None,
    ) -> dict[str, Any]:
        effective_actor_id = actor_id or owner_player_id
        if not effective_actor_id:
            raise TournamentError("forbidden", "Actor identity required.", 403)
        with self._database.sessions.begin() as session:
            tournament = session.get(Tournament, tournament_id)
            if tournament is None:
                raise TournamentError("tournament_not_found", "Tournament not found.", 404)
            if tournament.owner_player_id != effective_actor_id:
                raise TournamentError("forbidden", "Only the tournament owner can update settings.", 403)
            if tournament.status not in {"draft", "registration_open"}:
                raise TournamentError("invalid_state", "Cannot update tournament once registration is closed.", 409)

            changes: dict[str, Any] = {}
            if name is not None:
                clean = name.strip()
                if not 1 <= len(clean) <= 80:
                    raise TournamentError("invalid_tournament_name", "Tournament name must be 1 to 80 characters.", 422)
                tournament.name = clean
                changes["name"] = clean

            if description is not None:
                tournament.description = description.strip() if description else None
                changes["description"] = tournament.description

            if capacity is not None:
                if not 2 <= capacity <= 64:
                    raise TournamentError("invalid_capacity", "Capacity must be between 2 and 64 players.", 422)
                reg_count = session.scalar(
                    select(func.count()).select_from(TournamentParticipant)
                    .where(TournamentParticipant.tournament_id == tournament_id, TournamentParticipant.status == "registered")
                ) or 0
                if capacity < reg_count:
                    raise TournamentError("capacity_conflict", f"Cannot reduce capacity below current registered count ({reg_count}).", 409)
                tournament.capacity = capacity
                changes["capacity"] = capacity

            if registration_opens_at is not None:
                tournament.registration_opens_at = registration_opens_at
                changes["registration_opens_at"] = str(registration_opens_at)
            if registration_closes_at is not None:
                tournament.registration_closes_at = registration_closes_at
                changes["registration_closes_at"] = str(registration_closes_at)
            if scheduled_start_at is not None:
                tournament.scheduled_start_at = scheduled_start_at
                changes["scheduled_start_at"] = str(scheduled_start_at)
            if configuration is not None:
                tournament.configuration = configuration
                changes["configuration"] = configuration

            tournament.updated_at = utc_now()
            self._record_audit_event(
                session, tournament_id, actor_id, "tournament_updated", "Owner updated tournament settings", changes,
            )

        return self.get_tournament(tournament_id)

    def transition_state(self, tournament_id: str, actor_id: str, new_status: str, reason: str | None = None) -> dict[str, Any]:
        with self._database.sessions.begin() as session:
            tournament = session.get(Tournament, tournament_id)
            if tournament is None:
                raise TournamentError("tournament_not_found", "Tournament not found.", 404)
            if tournament.owner_player_id != actor_id:
                raise TournamentError("forbidden", "Only the tournament owner can change lifecycle status.", 403)

            allowed = ALLOWED_TRANSITIONS.get(tournament.status, set())
            if new_status not in allowed:
                raise TournamentError(
                    "invalid_transition",
                    f"Cannot transition tournament from '{tournament.status}' to '{new_status}'.",
                    409,
                )

            old_status = tournament.status
            tournament.status = new_status
            tournament.updated_at = utc_now()
            if new_status == "in_progress" and tournament.started_at is None:
                tournament.started_at = utc_now()
            elif new_status == "completed" and tournament.completed_at is None:
                tournament.completed_at = utc_now()

            self._record_audit_event(
                session, tournament_id, actor_id, f"status_{new_status}", reason,
                {"from_status": old_status, "to_status": new_status},
            )

        return self.get_tournament(tournament_id)

    def open_registration(self, tournament_id: str, actor_id: str | None = None, owner_player_id: str | None = None) -> dict[str, Any]:
        return self.transition_state(tournament_id, actor_id or owner_player_id or "", "registration_open", "Owner opened registration")

    def close_registration(self, tournament_id: str, actor_id: str | None = None, owner_player_id: str | None = None) -> dict[str, Any]:
        return self.transition_state(tournament_id, actor_id or owner_player_id or "", "registration_closed", "Owner closed registration")

    def cancel_tournament(self, tournament_id: str, actor_id: str | None = None, reason: str = "Cancelled by owner", owner_player_id: str | None = None) -> dict[str, Any]:
        return self.transition_state(tournament_id, actor_id or owner_player_id or "", "cancelled", reason)

    # -------------------------------------------------------------------------
    # Registration & Withdrawal
    # -------------------------------------------------------------------------

    def register_player(self, tournament_id: str, player_id: str) -> dict[str, Any]:
        now = utc_now()
        with self._database.sessions.begin() as session:
            tournament = session.get(Tournament, tournament_id)
            if tournament is None:
                raise TournamentError("tournament_not_found", "Tournament not found.", 404)
            if tournament.status != "registration_open":
                raise TournamentError("registration_not_open", f"Tournament is not open for registration (status: {tournament.status}).", 409)

            # Check registration window
            if tournament.registration_opens_at and now < tournament.registration_opens_at:
                raise TournamentError("registration_not_started", "Registration has not opened yet.", 409)
            if tournament.registration_closes_at and now > tournament.registration_closes_at:
                raise TournamentError("registration_closed", "Registration deadline has passed.", 409)

            player = session.get(PlayerProfile, player_id)
            if player is None or player.status != "active":
                raise TournamentError("player_unavailable", "Active player profile is required.", 404)

            # Idempotency check: if already registered, return existing
            existing = session.scalar(
                select(TournamentParticipant).where(
                    TournamentParticipant.tournament_id == tournament_id,
                    TournamentParticipant.player_id == player_id,
                )
            )
            if existing is not None:
                if existing.status == "registered":
                    return self._participant_view(existing, player)
                # Re-activate if previously withdrawn
                existing.status = "registered"
                existing.registered_at = now
                tournament.updated_at = now
                self._record_audit_event(session, tournament_id, player_id, "player_re_registered", None, {})
                return self._participant_view(existing, player)

            # Check capacity
            current_count = session.scalar(
                select(func.count()).select_from(TournamentParticipant)
                .where(TournamentParticipant.tournament_id == tournament_id, TournamentParticipant.status == "registered")
            ) or 0
            if current_count >= tournament.capacity:
                raise TournamentError("tournament_full", "Tournament has reached full capacity.", 409)

            part_id = secrets.token_hex(16)
            participant = TournamentParticipant(
                id=part_id,
                tournament_id=tournament_id,
                player_id=player_id,
                registered_at=now,
                status="registered",
            )
            session.add(participant)
            tournament.updated_at = now

            self._record_audit_event(
                session, tournament_id, player_id, "player_registered", None,
                {"slot": current_count + 1, "capacity": tournament.capacity},
            )
            tourn_title = tournament.name

        if self.notification_service is not None:
            self.notification_service.create_notification(
                player_id,
                "tournament_registration_confirmed",
                "Tournament Registration Confirmed",
                f"You have registered for {tourn_title}.",
                target_type="tournament",
                target_id=tournament_id,
                deduplication_key=f"tourn_reg:{tournament_id}:{player_id}",
                source_event_id=tournament_id,
            )

        return self._participant_view(participant, player)

    def withdraw_player(self, tournament_id: str, player_id: str) -> dict[str, Any]:
        with self._database.sessions.begin() as session:
            tournament = session.get(Tournament, tournament_id)
            if tournament is None:
                raise TournamentError("tournament_not_found", "Tournament not found.", 404)
            if tournament.status != "registration_open":
                raise TournamentError("cannot_withdraw", "Cannot withdraw after registration has closed.", 409)

            participant = session.scalar(
                select(TournamentParticipant).where(
                    TournamentParticipant.tournament_id == tournament_id,
                    TournamentParticipant.player_id == player_id,
                )
            )
            if participant is None or participant.status != "registered":
                raise TournamentError("not_registered", "Player is not registered in this tournament.", 404)

            participant.status = "withdrawn"
            tournament.updated_at = utc_now()
            self._record_audit_event(session, tournament_id, player_id, "player_withdrawn", None, {})

        return {"ok": True, "tournament_id": tournament_id, "player_id": player_id, "status": "withdrawn"}

    # -------------------------------------------------------------------------
    # Bracket Generation & Tournament Start
    # -------------------------------------------------------------------------

    def start_tournament(self, tournament_id: str, actor_id: str | None = None, owner_player_id: str | None = None) -> dict[str, Any]:
        now = utc_now()
        effective_actor_id = actor_id or owner_player_id or ""
        with self._database.sessions.begin() as session:
            tournament = session.get(Tournament, tournament_id)
            if tournament is None:
                raise TournamentError("tournament_not_found", "Tournament not found.", 404)
            if tournament.owner_player_id != effective_actor_id:
                raise TournamentError("forbidden", "Only the tournament owner can start the tournament.", 403)
            if tournament.status not in {"registration_open", "registration_closed"}:
                raise TournamentError("invalid_state", f"Cannot start tournament from status '{tournament.status}'.", 409)

            # Get registered participants
            participants = session.scalars(
                select(TournamentParticipant)
                .where(TournamentParticipant.tournament_id == tournament_id, TournamentParticipant.status == "registered")
            ).all()

            count = len(participants)
            if count < 2:
                raise TournamentError("insufficient_participants", f"At least 2 participants required to start (currently {count}).", 409)

            # Deterministic seeding: mode rating (primary) -> global rating (secondary) -> registration time -> player_id
            pids = [p.player_id for p in participants]
            mode_scope = f"mode:{tournament.mode}"
            ratings = session.scalars(
                select(PlayerRating).where(
                    PlayerRating.player_id.in_(pids),
                    PlayerRating.rating_scope.in_([mode_scope, "global"]),
                )
            ).all()
            mode_ratings: dict[str, int] = {}
            global_ratings: dict[str, int] = {}
            for r in ratings:
                if r.rating_scope == mode_scope:
                    mode_ratings[r.player_id] = r.rating
                elif r.rating_scope == "global":
                    global_ratings[r.player_id] = r.rating

            sorted_participants = sorted(
                participants,
                key=lambda p: (
                    -(mode_ratings.get(p.player_id, global_ratings.get(p.player_id, 1000))),
                    -(global_ratings.get(p.player_id, 1000)),
                    _as_utc(p.registered_at),
                    p.player_id,
                ),
            )

            # Assign seeds 1..count
            for seed_idx, p in enumerate(sorted_participants, start=1):
                p.seed = seed_idx
                p.status = "active"

            # Compute bracket size and byes
            power_of_two = 1
            num_rounds = 0
            while power_of_two < count:
                power_of_two *= 2
                num_rounds += 1

            bracket_slots = get_bracket_slots(power_of_two)
            # Map seed -> player_id (or None if bye)
            seed_to_player: dict[int, str] = {p.seed: p.player_id for p in sorted_participants if p.seed}

            # Create Rounds & Matches
            rounds: list[TournamentRound] = []
            matches_by_round: dict[int, list[TournamentMatch]] = {}

            matches_in_current_round = power_of_two // 2
            for r_num in range(1, num_rounds + 1):
                round_id = secrets.token_hex(16)
                t_round = TournamentRound(
                    id=round_id,
                    tournament_id=tournament_id,
                    round_number=r_num,
                    status="in_progress" if r_num == 1 else "pending",
                    scheduled_at=now,
                )
                session.add(t_round)
                rounds.append(t_round)
                matches_by_round[r_num] = []

                for m_idx in range(1, matches_in_current_round + 1):
                    match_id = secrets.token_hex(16)
                    m = TournamentMatch(
                        id=match_id,
                        tournament_id=tournament_id,
                        round_id=round_id,
                        match_number=m_idx,
                        status="pending",
                        created_at=now,
                    )
                    session.add(m)
                    matches_by_round[r_num].append(m)

                matches_in_current_round //= 2

            session.flush()

            # Populate Round 1 matches from seed slots
            round_1_matches = matches_by_round[1]
            bye_advancements: list[tuple[str, int]] = []  # (winner_player_id, match_number)

            for m_idx, match in enumerate(round_1_matches):
                slot1_seed = bracket_slots[m_idx * 2]
                slot2_seed = bracket_slots[m_idx * 2 + 1]

                p1_id = seed_to_player.get(slot1_seed)
                p2_id = seed_to_player.get(slot2_seed)

                match.player_one_id = p1_id
                match.player_two_id = p2_id

                if p1_id and p2_id:
                    match.status = "scheduled"
                    match.scheduled_at = now
                elif p1_id and not p2_id:
                    # Player 1 has a BYE
                    match.status = "bye"
                    match.winner_player_id = p1_id
                    match.result_reason = "bye"
                    match.completed_at = now
                    bye_advancements.append((p1_id, match.match_number))
                elif not p1_id and p2_id:
                    # Player 2 has a BYE
                    match.status = "bye"
                    match.winner_player_id = p2_id
                    match.result_reason = "bye"
                    match.completed_at = now
                    bye_advancements.append((p2_id, match.match_number))

            session.flush()

            # Advance byes into Round 2 immediately
            if num_rounds > 1 and bye_advancements:
                round_2_matches = matches_by_round[2]
                for winner_id, m_num in bye_advancements:
                    next_m_idx = math.ceil(m_num / 2) - 1
                    target_match = round_2_matches[next_m_idx]
                    if m_num % 2 == 1:
                        target_match.player_one_id = winner_id
                    else:
                        target_match.player_two_id = winner_id

                    if target_match.player_one_id and target_match.player_two_id:
                        target_match.status = "scheduled"
                        target_match.scheduled_at = now

            # If all round 1 matches were byes (e.g. only 2 players -> handled; if more byes advance)
            tournament.status = "in_progress"
            tournament.started_at = now
            tournament.updated_at = now

            seed_summary = {p.player_id: {"seed": p.seed, "rating": mode_ratings.get(p.player_id, global_ratings.get(p.player_id, 1000))} for p in sorted_participants}
            self._record_audit_event(
                session, tournament_id, actor_id, "tournament_started", "Bracket generated and tournament started",
                {"participant_count": count, "rounds": num_rounds, "byes": power_of_two - count, "seeds": seed_summary},
            )

        return self.get_bracket(tournament_id)

    # -------------------------------------------------------------------------
    # Fixture Admission Tickets & Match Launch
    # -------------------------------------------------------------------------

    def issue_admission_ticket(self, tournament_id: str, match_id: str, player_id: str) -> dict[str, Any]:
        now = utc_now()
        with self._database.sessions.begin() as session:
            tournament = session.get(Tournament, tournament_id)
            if tournament is None:
                raise TournamentError("tournament_not_found", "Tournament not found.", 404)
            if tournament.status != "in_progress":
                raise TournamentError("tournament_not_active", "Tournament is not currently in progress.", 409)

            match = session.get(TournamentMatch, match_id)
            if match is None or match.tournament_id != tournament_id:
                raise TournamentError("match_not_found", "Tournament match not found.", 404)

            if player_id not in {match.player_one_id, match.player_two_id}:
                raise TournamentError("player_mismatch", "You are not an assigned player in this match fixture.", 403)

            if match.status not in {"scheduled", "in_progress"}:
                raise TournamentError("match_not_ready", f"Match is not ready for admission (status: {match.status}).", 409)

            # Invalidate any existing unused ticket for this player and match
            session.execute(
                update(TournamentFixtureTicket)
                .where(
                    TournamentFixtureTicket.tournament_match_id == match_id,
                    TournamentFixtureTicket.player_id == player_id,
                    TournamentFixtureTicket.used_at.is_(None),
                )
                .values(used_at=now)
            )

            raw_token = secrets.token_urlsafe(32)
            ticket_hash = _token_hash(raw_token)
            expires_at = now + timedelta(seconds=60)

            ticket_record = TournamentFixtureTicket(
                id=secrets.token_hex(16),
                tournament_id=tournament_id,
                tournament_match_id=match_id,
                player_id=player_id,
                token_hash=ticket_hash,
                expires_at=expires_at,
                created_at=now,
            )
            session.add(ticket_record)

        return {
            "ticket": raw_token,
            "tournament_id": tournament_id,
            "match_id": match_id,
            "player_id": player_id,
            "expires_in_seconds": 60,
        }

    issue_fixture_admission_ticket = issue_admission_ticket

    def validate_and_consume_ticket(self, tournament_id: str, match_id: str, player_id: str, token: str) -> bool:
        now = utc_now()
        digest = _token_hash(token)
        with self._database.sessions.begin() as session:
            ticket = session.scalar(
                select(TournamentFixtureTicket).where(
                    TournamentFixtureTicket.token_hash == digest,
                    TournamentFixtureTicket.tournament_id == tournament_id,
                    TournamentFixtureTicket.tournament_match_id == match_id,
                    TournamentFixtureTicket.player_id == player_id,
                )
            )
            if ticket is None:
                raise TournamentError("invalid_fixture_ticket", "Admission ticket is invalid or does not match.", 401)
            if ticket.used_at is not None:
                raise TournamentError("ticket_already_used", "Admission ticket has already been used.", 401)
            if _as_utc(ticket.expires_at) <= now:
                raise TournamentError("ticket_expired", "Admission ticket has expired. Request a new one.", 401)

            ticket.used_at = now
            return True

    async def launch_or_join_fixture(
        self,
        engine,
        tournament_id: str,
        match_id: str,
        player_id: str,
        player_name: str,
        token: str,
    ) -> dict[str, Any]:
        # Validate ticket first
        self.validate_and_consume_ticket(tournament_id, match_id, player_id, token)

        with self._database.session() as session:
            tournament = session.get(Tournament, tournament_id)
            match = session.get(TournamentMatch, match_id)
            if tournament is None or match is None:
                raise TournamentError("fixture_not_found", "Tournament fixture not found.", 404)
            mode = tournament.mode

        room_code = self._match_rooms.get(match_id)
        if room_code is None:
            # First player creates the authoritative room with frozen settings
            frozen_selection = {
                "mode": mode,
                "question_count": 5,
                "tournament_id": tournament_id,
                "tournament_match_id": match_id,
            }
            joined = await engine.create_room(
                player_name,
                mode=mode,
                selection=frozen_selection,
                profile_id=player_id,
            )
            self._match_rooms[match_id] = joined["state"]["code"]
            return joined
        else:
            # Second player joins the room
            joined = await engine.join_room(room_code, player_name, profile_id=player_id, is_fixture_admission=True)
            # Transition match to in_progress in DB
            with self._database.sessions.begin() as session:
                m = session.get(TournamentMatch, match_id)
                if m and m.status == "scheduled":
                    m.status = "in_progress"
            return joined

    # -------------------------------------------------------------------------
    # Match Result Processing & Advancement
    # -------------------------------------------------------------------------

    def process_match_completion(
        self,
        session,
        tournament_match_id: str,
        official_match_id: str,
        result_payload: dict[str, Any],
    ) -> bool:
        """Processes official match completion, advances bracket winner, crowns champion."""
        match = session.get(TournamentMatch, tournament_match_id)
        if match is None:
            return False
        if match.status in {"completed", "forfeited"}:
            return False  # Idempotent: already completed

        participants = result_payload.get("participants", [])
        p_ids = {p.get("player_id") for p in participants if p.get("player_id")}
        if not {match.player_one_id, match.player_two_id}.issubset(p_ids):
            # Participants mismatch
            match.status = "disputed"
            match.result_reason = "participant_mismatch"
            return False

        # Find winner from placement
        winners = [p for p in participants if p.get("placement") == 1]
        now = utc_now()

        if len(winners) == 1:
            winner_id = winners[0]["player_id"]
            loser_id = match.player_two_id if winner_id == match.player_one_id else match.player_one_id

            match.status = "completed"
            match.winner_player_id = winner_id
            match.official_match_id = official_match_id
            match.result_reason = "normal"
            match.completed_at = now

            tournament = session.get(Tournament, match.tournament_id)
            total_rounds = session.scalar(
                select(func.count()).select_from(TournamentRound)
                .where(TournamentRound.tournament_id == match.tournament_id)
            ) or 1

            current_round_num = match.round.round_number

            if current_round_num >= total_rounds:
                # FINAL MATCH: Crown Champion and Runner-up
                champ = session.scalar(
                    select(TournamentParticipant).where(
                        TournamentParticipant.tournament_id == match.tournament_id,
                        TournamentParticipant.player_id == winner_id,
                    )
                )
                if champ:
                    champ.status = "champion"
                    champ.final_placement = 1

                runner = session.scalar(
                    select(TournamentParticipant).where(
                        TournamentParticipant.tournament_id == match.tournament_id,
                        TournamentParticipant.player_id == loser_id,
                    )
                )
                if runner:
                    runner.status = "runner_up"
                    runner.final_placement = 2

                tournament.status = "completed"
                tournament.completed_at = now
                tournament.updated_at = now
                match.round.status = "completed"
                match.round.completed_at = now

                self._record_audit_event(
                    session, match.tournament_id, None, "tournament_completed", "Final match completed",
                    {"champion_id": winner_id, "runner_up_id": loser_id, "match_id": tournament_match_id},
                )
            else:
                # Advance winner to next round
                loser_part = session.scalar(
                    select(TournamentParticipant).where(
                        TournamentParticipant.tournament_id == match.tournament_id,
                        TournamentParticipant.player_id == loser_id,
                    )
                )
                if loser_part:
                    loser_part.status = "eliminated"

                next_round = session.scalar(
                    select(TournamentRound).where(
                        TournamentRound.tournament_id == match.tournament_id,
                        TournamentRound.round_number == current_round_num + 1,
                    )
                )
                if next_round:
                    target_match_idx = math.ceil(match.match_number / 2)
                    next_match = session.scalar(
                        select(TournamentMatch).where(
                            TournamentMatch.round_id == next_round.id,
                            TournamentMatch.match_number == target_match_idx,
                        )
                    )
                    if next_match:
                        if match.match_number % 2 == 1:
                            next_match.player_one_id = winner_id
                        else:
                            next_match.player_two_id = winner_id

                        if next_match.player_one_id and next_match.player_two_id:
                            next_match.status = "scheduled"
                            next_match.scheduled_at = now

                # Check if current round completed
                pending_in_round = session.scalar(
                    select(func.count()).select_from(TournamentMatch)
                    .where(
                        TournamentMatch.round_id == match.round_id,
                        TournamentMatch.status.in_(["pending", "scheduled", "in_progress"]),
                    )
                ) or 0
                if pending_in_round == 0:
                    match.round.status = "completed"
                    match.round.completed_at = now
                    if next_round:
                        next_round.status = "in_progress"

                self._record_audit_event(
                    session, match.tournament_id, None, "match_completed", "Match won and winner advanced",
                    {"match_id": tournament_match_id, "winner_id": winner_id, "round": current_round_num},
                )

            tourn_title = tournament.name
            tourn_id = match.tournament_id
            is_final = current_round_num >= total_rounds

            if self.notification_service is not None and winner_id and loser_id:
                if is_final:
                    self.notification_service.create_notification(
                        winner_id,
                        "new_trophy",
                        "🏆 Tournament Champion!",
                        f"Congratulations! You won the championship in {tourn_title}!",
                        target_type="tournament",
                        target_id=tourn_id,
                        metadata={"tournament_id": tourn_id, "title": tourn_title, "tier": "gold"},
                        deduplication_key=f"tourn_champ:{tourn_id}:{winner_id}",
                        source_event_id=tourn_id,
                    )
                    self.notification_service.create_notification(
                        loser_id,
                        "new_trophy",
                        "🥈 Tournament Runner-Up!",
                        f"Great run! You finished as Runner-Up in {tourn_title}!",
                        target_type="tournament",
                        target_id=tourn_id,
                        metadata={"tournament_id": tourn_id, "title": tourn_title, "tier": "silver"},
                        deduplication_key=f"tourn_runner:{tourn_id}:{loser_id}",
                        source_event_id=tourn_id,
                    )
                else:
                    self.notification_service.create_notification(
                        winner_id,
                        "tournament_advanced",
                        "Tournament Match Won",
                        f"You won your match and advanced to the next round in {tourn_title}!",
                        target_type="tournament",
                        target_id=tourn_id,
                        deduplication_key=f"tourn_adv:{tournament_match_id}:{winner_id}",
                        source_event_id=tournament_match_id,
                    )
                    self.notification_service.create_notification(
                        loser_id,
                        "tournament_eliminated",
                        "Tournament Match Result",
                        f"You have been eliminated from {tourn_title}.",
                        target_type="tournament",
                        target_id=tourn_id,
                        deduplication_key=f"tourn_elim:{tournament_match_id}:{loser_id}",
                        source_event_id=tournament_match_id,
                    )

            return True
        else:
            # Tie policy: mark match as disputed / tiebreak pending
            match.status = "disputed"
            match.result_reason = "tiebreak_required"
            self._record_audit_event(
                session, match.tournament_id, None, "match_tiebreak_required", "Match ended in a tie",
                {"match_id": tournament_match_id},
            )
            return False

    def forfeit_match(self, tournament_id: str, match_id: str, forfeiting_player_id: str, reason: str = "no_show") -> dict[str, Any]:
        now = utc_now()
        with self._database.sessions.begin() as session:
            match = session.get(TournamentMatch, match_id)
            if match is None or match.tournament_id != tournament_id:
                raise TournamentError("match_not_found", "Tournament match not found.", 404)
            if match.status not in {"scheduled", "in_progress", "pending", "disputed"}:
                raise TournamentError("cannot_forfeit", f"Cannot forfeit match in status '{match.status}'.", 409)

            if forfeiting_player_id not in {match.player_one_id, match.player_two_id}:
                raise TournamentError("player_mismatch", "Player is not a participant in this match.", 403)

            winner_id = match.player_two_id if forfeiting_player_id == match.player_one_id else match.player_one_id
            if not winner_id:
                raise TournamentError("invalid_forfeit", "Cannot resolve forfeit without an opposing player.", 409)

            match.status = "forfeited"
            match.winner_player_id = winner_id
            match.result_reason = f"forfeit_{reason}"
            match.completed_at = now

            # Advance winner
            total_rounds = session.scalar(
                select(func.count()).select_from(TournamentRound).where(TournamentRound.tournament_id == tournament_id)
            ) or 1
            current_round_num = match.round.round_number

            if current_round_num >= total_rounds:
                tournament = session.get(Tournament, tournament_id)
                tournament.status = "completed"
                tournament.completed_at = now
                champ = session.scalar(select(TournamentParticipant).where(TournamentParticipant.tournament_id == tournament_id, TournamentParticipant.player_id == winner_id))
                runner = session.scalar(select(TournamentParticipant).where(TournamentParticipant.tournament_id == tournament_id, TournamentParticipant.player_id == forfeiting_player_id))
                if champ: champ.status, champ.final_placement = "champion", 1
                if runner: runner.status, runner.final_placement = "runner_up", 2
            else:
                next_round = session.scalar(select(TournamentRound).where(TournamentRound.tournament_id == tournament_id, TournamentRound.round_number == current_round_num + 1))
                if next_round:
                    target_match_idx = math.ceil(match.match_number / 2)
                    next_match = session.scalar(select(TournamentMatch).where(TournamentMatch.round_id == next_round.id, TournamentMatch.match_number == target_match_idx))
                    if next_match:
                        if match.match_number % 2 == 1: next_match.player_one_id = winner_id
                        else: next_match.player_two_id = winner_id
                        if next_match.player_one_id and next_match.player_two_id:
                            next_match.status, next_match.scheduled_at = "scheduled", now

            self._record_audit_event(
                session, tournament_id, forfeiting_player_id, "match_forfeited", f"Player forfeited due to {reason}",
                {"match_id": match_id, "winner_id": winner_id, "forfeiting_id": forfeiting_player_id},
            )

        return self.get_bracket(tournament_id)

    def file_dispute(
        self,
        tournament_id: str,
        match_id: str,
        player_id: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._database.sessions.begin() as session:
            match = session.get(TournamentMatch, match_id)
            if match is None or match.tournament_id != tournament_id:
                raise TournamentError("match_not_found", "Tournament match not found.", 404)
            if player_id not in {match.player_one_id, match.player_two_id}:
                raise TournamentError("player_mismatch", "Only assigned match participants can file disputes.", 403)
            match.status = "disputed"
            match.result_reason = f"dispute_{reason[:32]}"
            self._record_audit_event(
                session, tournament_id, player_id, "match_disputed", reason,
                {"match_id": match_id, "disputing_player_id": player_id, "details": details or {}},
            )
            return {"match_id": match_id, "status": match.status, "reason": reason}

    def get_audit_trail(self, tournament_id: str) -> list[dict[str, Any]]:
        with self._database.session() as session:
            events = session.scalars(
                select(TournamentAuditEvent)
                .where(TournamentAuditEvent.tournament_id == tournament_id)
                .order_by(TournamentAuditEvent.timestamp.asc())
            ).all()
            return [
                {
                    "id": ev.id,
                    "tournament_id": ev.tournament_id,
                    "actor_player_id": ev.actor_player_id,
                    "action": ev.action,
                    "reason": ev.reason,
                    "timestamp": ev.timestamp,
                    "structured_summary": ev.structured_summary,
                }
                for ev in events
            ]

    # -------------------------------------------------------------------------
    # Queries & Views
    # -------------------------------------------------------------------------

    def list_tournaments(
        self,
        status: str | None = None,
        mode: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        with self._database.session() as session:
            query = select(Tournament).join(PlayerProfile, PlayerProfile.id == Tournament.owner_player_id)
            count_query = select(func.count()).select_from(Tournament)

            if status:
                query = query.where(Tournament.status == status)
                count_query = count_query.where(Tournament.status == status)
            if mode:
                query = query.where(Tournament.mode == mode)
                count_query = count_query.where(Tournament.mode == mode)

            total = int(session.scalar(count_query) or 0)
            rows = session.scalars(
                query.order_by(Tournament.created_at.desc(), Tournament.id.desc())
                .limit(limit).offset(offset)
            ).all()

            tournaments = [self._tournament_summary_view(t, session) for t in rows]
            return {"total": total, "limit": limit, "offset": offset, "tournaments": tournaments}

    def get_tournament(self, tournament_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            t = session.get(Tournament, tournament_id)
            if t is None:
                raise TournamentError("tournament_not_found", "Tournament not found.", 404)
            return self._tournament_detail_view(t, session)

    def get_bracket(self, tournament_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            t = session.get(Tournament, tournament_id)
            if t is None:
                raise TournamentError("tournament_not_found", "Tournament not found.", 404)

            rounds = session.scalars(
                select(TournamentRound)
                .where(TournamentRound.tournament_id == tournament_id)
                .order_by(TournamentRound.round_number.asc())
            ).all()

            bracket_rounds = []
            for r in rounds:
                matches = session.scalars(
                    select(TournamentMatch)
                    .where(TournamentMatch.round_id == r.id)
                    .options(
                        selectinload(TournamentMatch.player_one),
                        selectinload(TournamentMatch.player_two),
                        selectinload(TournamentMatch.winner),
                    )
                    .order_by(TournamentMatch.match_number.asc())
                ).all()
                bracket_rounds.append({
                    "id": r.id,
                    "round_number": r.round_number,
                    "status": r.status,
                    "scheduled_at": r.scheduled_at,
                    "completed_at": r.completed_at,
                    "matches": [self._match_view(m) for m in matches],
                })

            participant_rows = session.execute(
                select(TournamentParticipant, PlayerProfile)
                .join(PlayerProfile, PlayerProfile.id == TournamentParticipant.player_id)
                .where(TournamentParticipant.tournament_id == tournament_id)
                .order_by(TournamentParticipant.seed.asc().nullslast(), TournamentParticipant.registered_at.asc())
            ).all()

            return {
                "tournament": self._tournament_summary_view(t, session),
                "rounds": bracket_rounds,
                "participants": [self._participant_view(p, profile) for p, profile in participant_rows],
            }

    def get_match(self, tournament_id: str, match_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            m = session.scalar(
                select(TournamentMatch)
                .where(TournamentMatch.id == match_id, TournamentMatch.tournament_id == tournament_id)
                .options(
                    selectinload(TournamentMatch.player_one),
                    selectinload(TournamentMatch.player_two),
                    selectinload(TournamentMatch.winner),
                )
            )
            if m is None:
                raise TournamentError("match_not_found", "Tournament match not found.", 404)
            return self._match_view(m)

    def list_matches(self, tournament_id: str, round_id: str | None = None) -> list[dict[str, Any]]:
        with self._database.session() as session:
            query = (
                select(TournamentMatch)
                .where(TournamentMatch.tournament_id == tournament_id)
                .options(
                    selectinload(TournamentMatch.player_one),
                    selectinload(TournamentMatch.player_two),
                    selectinload(TournamentMatch.winner),
                )
            )
            if round_id:
                query = query.where(TournamentMatch.round_id == round_id)
            matches = session.scalars(query.order_by(TournamentMatch.round_id.asc(), TournamentMatch.match_number.asc())).all()
            return [self._match_view(m) for m in matches]

    # -------------------------------------------------------------------------
    # Internal Helpers & Views
    # -------------------------------------------------------------------------

    @staticmethod
    def _record_audit_event(session, tournament_id: str, actor_id: str | None, action: str, reason: str | None, summary: dict[str, Any]) -> None:
        event = TournamentAuditEvent(
            id=secrets.token_hex(16),
            tournament_id=tournament_id,
            actor_player_id=actor_id,
            action=action,
            reason=reason,
            timestamp=utc_now(),
            structured_summary=summary,
        )
        session.add(event)

    @staticmethod
    def _participant_view(p: TournamentParticipant, profile: PlayerProfile | None = None) -> dict[str, Any]:
        return {
            "id": p.id,
            "tournament_id": p.tournament_id,
            "player_id": p.player_id,
            "display_name": profile.display_name if profile else "Unknown",
            "seed": p.seed,
            "status": p.status,
            "final_placement": p.final_placement,
            "registered_at": p.registered_at,
        }

    @staticmethod
    def _match_view(m: TournamentMatch) -> dict[str, Any]:
        return {
            "id": m.id,
            "tournament_id": m.tournament_id,
            "round_id": m.round_id,
            "match_number": m.match_number,
            "player_one": {"id": m.player_one.id, "display_name": m.player_one.display_name} if m.player_one else None,
            "player_two": {"id": m.player_two.id, "display_name": m.player_two.display_name} if m.player_two else None,
            "winner": {"id": m.winner.id, "display_name": m.winner.display_name} if m.winner else None,
            "status": m.status,
            "scheduled_at": m.scheduled_at,
            "official_match_id": m.official_match_id,
            "result_reason": m.result_reason,
            "created_at": m.created_at,
            "completed_at": m.completed_at,
        }

    @staticmethod
    def _tournament_summary_view(t: Tournament, session) -> dict[str, Any]:
        count = session.scalar(
            select(func.count()).select_from(TournamentParticipant)
            .where(TournamentParticipant.tournament_id == t.id, TournamentParticipant.status.in_(["registered", "active", "champion", "runner_up"]))
        ) or 0
        return {
            "id": t.id,
            "owner_player_id": t.owner_player_id,
            "owner_display_name": t.owner.display_name if t.owner else "Unknown",
            "name": t.name,
            "description": t.description,
            "format": t.format,
            "mode": t.mode,
            "status": t.status,
            "capacity": t.capacity,
            "registered_count": count,
            "registration_opens_at": t.registration_opens_at,
            "registration_closes_at": t.registration_closes_at,
            "scheduled_start_at": t.scheduled_start_at,
            "started_at": t.started_at,
            "completed_at": t.completed_at,
            "created_at": t.created_at,
        }

    def _tournament_detail_view(self, t: Tournament, session) -> dict[str, Any]:
        base = self._tournament_summary_view(t, session)
        base["rules_version"] = t.rules_version
        base["configuration"] = t.configuration
        base["updated_at"] = t.updated_at
        return base

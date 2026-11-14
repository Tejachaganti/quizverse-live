"""Pseudonymous player credentials and trusted completed-match history."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    CompetitiveMatch,
    MatchParticipant,
    MatchTeam,
    PlayerCredential,
    PlayerProfile,
)
from quizverse.persistence.ratings import RatingService


class PlayerIdentityError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class MatchResultError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class IssuedPlayerCredential:
    player_id: str
    display_name: str
    version: int
    created_at: datetime
    updated_at: datetime
    token: str

    def __repr__(self) -> str:
        return "IssuedPlayerCredential(player_id=<redacted>, display_name=<redacted>, version=<redacted>, token=<redacted>)"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", errors="strict")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def normalize_display_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Display name must be text.")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError("Display name contains unsupported characters.")
    name = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(name) <= 24:
        raise ValueError("Display name must contain 1 to 24 characters.")
    if not any(character.isalnum() for character in name):
        raise ValueError("Display name must include a letter or number.")
    return name


class PlayerIdentityService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, raw_name: object) -> IssuedPlayerCredential:
        try:
            name = normalize_display_name(raw_name)
        except ValueError as error:
            raise PlayerIdentityError("invalid_display_name", str(error)) from error
        player_id, credential_id, token, now = secrets.token_hex(16), secrets.token_hex(16), _new_token(), datetime.now(UTC)
        with self._database.sessions.begin() as session:
            session.add(PlayerProfile(id=player_id, display_name=name, status="active", version=1, created_at=now, updated_at=now))
            session.add(PlayerCredential(id=credential_id, player_id=player_id, token_hash=_token_hash(token), created_at=now))
        return IssuedPlayerCredential(player_id, name, 1, now, now, token)

    def authenticate(self, raw_token: object) -> dict[str, object]:
        if not isinstance(raw_token, str) or not 40 <= len(raw_token) <= 128:
            raise PlayerIdentityError("invalid_player_credential", "The player credential is invalid or revoked.")
        try:
            digest = _token_hash(raw_token)
        except UnicodeEncodeError as error:
            raise PlayerIdentityError("invalid_player_credential", "The player credential is invalid or revoked.") from error
        with self._database.session() as session:
            row = session.execute(
                select(PlayerCredential, PlayerProfile)
                .join(PlayerProfile, PlayerProfile.id == PlayerCredential.player_id)
                .where(PlayerCredential.token_hash == digest)
            ).first()
            if row is None:
                raise PlayerIdentityError("invalid_player_credential", "The player credential is invalid or revoked.")
            credential, player = row
            if (
                credential.revoked_at is not None
                or (credential.expires_at is not None and _as_utc(credential.expires_at) <= datetime.now(UTC))
                or player.status != "active"
                or not hmac.compare_digest(credential.token_hash, digest)
            ):
                raise PlayerIdentityError("invalid_player_credential", "The player credential is invalid or revoked.")
            return self._profile_view(player)

    def profile(self, player_id: str) -> dict[str, object] | None:
        with self._database.session() as session:
            player = session.get(PlayerProfile, player_id)
            return self._profile_view(player) if player else None

    def update_name(self, player_id: str, raw_name: object, expected_version: int) -> dict[str, object]:
        try:
            name = normalize_display_name(raw_name)
        except ValueError as error:
            raise PlayerIdentityError("invalid_display_name", str(error)) from error
        with self._database.sessions.begin() as session:
            result = session.execute(
                update(PlayerProfile)
                .where(PlayerProfile.id == player_id, PlayerProfile.status == "active", PlayerProfile.version == expected_version)
                .values(display_name=name, version=PlayerProfile.version + 1, updated_at=datetime.now(UTC))
            )
            if result.rowcount != 1:
                player = session.get(PlayerProfile, player_id)
                if player is None or player.status != "active":
                    raise PlayerIdentityError("player_not_found", "The player profile is unavailable.")
                raise PlayerIdentityError("version_conflict", "The profile changed. Refresh it and try again.")
            player = session.get(PlayerProfile, player_id)
            assert player is not None
            return self._profile_view(player)

    def rotate(self, player_id: str) -> IssuedPlayerCredential:
        credential_id, token, now = secrets.token_hex(16), _new_token(), datetime.now(UTC)
        with self._database.sessions.begin() as session:
            player = session.get(PlayerProfile, player_id)
            if player is None or player.status != "active":
                raise PlayerIdentityError("player_not_found", "The player profile is unavailable.")
            session.execute(
                update(PlayerCredential)
                .where(PlayerCredential.player_id == player_id, PlayerCredential.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            session.add(PlayerCredential(id=credential_id, player_id=player_id, token_hash=_token_hash(token), created_at=now))
            view = self._profile_view(player)
        return IssuedPlayerCredential(
            str(view["id"]), str(view["display_name"]), int(view["version"]),
            view["created_at"], view["updated_at"], token,
        )

    def anonymize(self, player_id: str) -> None:
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            player = session.get(PlayerProfile, player_id)
            if player is None:
                return
            if player.status == "anonymized":
                return
            player.display_name = "Deleted player"
            player.status = "anonymized"
            player.deleted_at = now
            player.updated_at = now
            player.version += 1
            session.execute(
                update(PlayerCredential)
                .where(PlayerCredential.player_id == player_id, PlayerCredential.revoked_at.is_(None))
                .values(revoked_at=now)
            )

    def is_active(self, player_id: str) -> bool:
        with self._database.session() as session:
            return session.scalar(select(PlayerProfile.id).where(PlayerProfile.id == player_id, PlayerProfile.status == "active")) is not None

    @staticmethod
    def _profile_view(player: PlayerProfile) -> dict[str, object]:
        return {
            "id": player.id,
            "display_name": player.display_name,
            "status": player.status,
            "version": player.version,
            "created_at": player.created_at,
            "updated_at": player.updated_at,
        }


class LiveIdentityTickets:
    """Single-use, short-lived bridge from HTTP profile auth to a new WebSocket seat."""

    def __init__(self, *, ttl_seconds: float = 30, clock=time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._tickets: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, player_id: str) -> str:
        raw = _new_token()
        now = self._clock()
        with self._lock:
            self._tickets = {key: value for key, value in self._tickets.items() if value[1] > now}
            self._tickets[_token_hash(raw)] = (player_id, now + self._ttl)
        return raw

    def consume(self, raw_ticket: object) -> str | None:
        if not isinstance(raw_ticket, str) or not 40 <= len(raw_ticket) <= 128:
            return None
        try:
            digest = _token_hash(raw_ticket)
        except UnicodeEncodeError:
            return None
        now = self._clock()
        with self._lock:
            value = self._tickets.pop(digest, None)
        if value is None or value[1] <= now:
            return None
        return value[0]


class ProcessRateLimiter:
    """Small bounded per-client limiter for profile creation, rotation and ticket issue."""

    def __init__(self, *, limit: int = 5, window_seconds: float = 60, clock=time.monotonic) -> None:
        self._limit, self._window, self._clock = limit, window_seconds, clock
        self._attempts: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = self._clock()
        with self._lock:
            if len(self._attempts) > 4096:
                self._attempts = {name: values for name, values in self._attempts.items() if values and now - values[-1] < self._window}
            attempts = self._attempts.setdefault(key, deque())
            while attempts and now - attempts[0] >= self._window:
                attempts.popleft()
            if len(attempts) >= self._limit:
                return False, max(1, int(self._window - (now - attempts[0])))
            attempts.append(now)
            return True, 0


class MatchHistoryService:
    """Persists engine-built terminal payloads and offers profile-scoped history."""

    def __init__(
        self,
        database: Database,
        rating_service: RatingService | None = None,
        tournament_service: Any | None = None,
        league_service: Any | None = None,
    ) -> None:
        self._database = database
        self._rating_service = rating_service or RatingService(database)
        self._tournament_service = tournament_service
        self._league_service = league_service

    @property
    def rating_service(self) -> RatingService:
        return self._rating_service

    @property
    def tournament_service(self) -> Any | None:
        return self._tournament_service

    @tournament_service.setter
    def tournament_service(self, service: Any | None) -> None:
        self._tournament_service = service

    @property
    def league_service(self) -> Any | None:
        return self._league_service

    @league_service.setter
    def league_service(self, service: Any | None) -> None:
        self._league_service = service

    @staticmethod
    def digest_result(result: dict[str, object]) -> str:
        canonical = json.dumps(
            result, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def persist_completed(self, result: dict[str, object]) -> bool:
        clean = self._validate(result)
        digest = self.digest_result(clean)
        with self._database.sessions.begin() as session:
            existing = session.scalar(select(CompetitiveMatch).where(CompetitiveMatch.engine_match_id == clean["engine_match_id"]))
            if existing is not None:
                if hmac.compare_digest(existing.result_digest, digest):
                    # Idempotent retry: if match is eligible but was not rated, attempt safe reconciliation
                    if not existing.is_rated and existing.rating_ineligible_reason is None:
                        eligible, reason = self._rating_service.check_eligibility(clean, session=session)
                        if eligible:
                            self._rating_service.apply_match_ratings(session, existing, existing.participants)
                        else:
                            existing.rating_ineligible_reason = reason
                    return False
                raise MatchResultError("A completed match ID was retried with conflicting result data.")
            ids = [entry["player_id"] for entry in clean["participants"] if entry["player_id"] is not None]
            if not ids:
                raise MatchResultError("A profile-bound participant is required for persistent match history.")
            if len(ids) != len(set(ids)):
                raise MatchResultError("A profile cannot occupy more than one participant seat in a match.")
            match_id = secrets.token_hex(16)
            context = clean["result_context"]
            match = CompetitiveMatch(
                id=match_id,
                engine_match_id=clean["engine_match_id"],
                mode=clean["mode"],
                status="completed",
                result_kind=clean["result_kind"],
                rules_version=clean["rules_version"],
                started_at=clean["started_at"],
                completed_at=clean["completed_at"],
                question_set_hash=clean["question_set_hash"],
                result_digest=digest,
                result_context=context,
                is_rated=False,
            )
            session.add(match)
            session.flush()
            participants = [
                MatchParticipant(
                    id=secrets.token_hex(16), match_id=match_id, player_id=entry["player_id"],
                    seat_index=entry["seat_index"], team_id=entry["team_id"], final_score=entry["final_score"],
                    correct_count=entry["correct_count"], answer_count=entry["answer_count"], placement=entry["placement"],
                    outcome=entry["outcome"], disconnect_count=entry["disconnect_count"],
                    reconnect_expired=entry["reconnect_expired"], forfeited=entry["forfeited"],
                ) for entry in clean["participants"]
            ]
            session.add_all(participants)
            session.add_all([
                MatchTeam(
                    id=secrets.token_hex(16), match_id=match_id, team_id=team["team_id"], score=team["score"],
                    correct_count=team["correct_count"], response_time_ms=team["response_time_ms"],
                    placement=team["placement"], outcome=team["outcome"],
                ) for team in clean["teams"]
            ])
            session.flush()

            # Atomic rating update in the same database transaction
            eligible, reason = self._rating_service.check_eligibility(clean, session=session)
            if eligible:
                self._rating_service.apply_match_ratings(session, match, participants)
            else:
                match.is_rated = False
                match.rating_ineligible_reason = reason

            # Process tournament bracket advancement if this match was a tournament fixture
            if self._tournament_service is not None:
                context_data = clean.get("result_context") or {}
                t_match_id = context_data.get("tournament_match_id")
                if t_match_id:
                    self._tournament_service.process_match_completion(
                        session=session,
                        tournament_match_id=str(t_match_id),
                        official_match_id=match_id,
                        result_payload=clean,
                    )

            # Process league fixture completion if this match was a league fixture
            if self._league_service is not None:
                context_data = clean.get("result_context") or {}
                l_fixture_id = context_data.get("league_fixture_id")
                if l_fixture_id:
                    self._league_service.process_fixture_completion(
                        session=session,
                        fixture_id=str(l_fixture_id),
                        official_match_id=match_id,
                        result_payload=clean,
                    )

            return True

    @staticmethod
    def _validate(source: dict[str, object]) -> dict[str, object]:
        modes = {"classic", "speed_challenge", "team_battle", "elimination", "solo_practice", "quiz_royale", "power_play", "bluff_master", "escape_room"}
        if not isinstance(source, dict) or source.get("source") != "game_engine":
            raise MatchResultError("Match result did not come from the trusted game engine.")
        if not isinstance(source.get("engine_match_id"), str) or len(source["engine_match_id"]) != 32:
            raise MatchResultError("Invalid engine match ID.")
        if source.get("mode") not in modes or source.get("status") != "completed":
            raise MatchResultError("Only supported completed game modes can be persisted.")
        if source.get("result_kind") not in {"individual", "team", "cooperative", "practice"}:
            raise MatchResultError("Invalid match result kind.")
        if not isinstance(source.get("rules_version"), str) or not 1 <= len(source["rules_version"]) <= 32:
            raise MatchResultError("Invalid rules version.")
        started, completed = source.get("started_at"), source.get("completed_at")
        if not isinstance(started, datetime) or not isinstance(completed, datetime):
            raise MatchResultError("Invalid match timestamps.")
        started, completed = _as_utc(started), _as_utc(completed)
        if completed < started:
            raise MatchResultError("Invalid match timestamps.")
        qhash = source.get("question_set_hash")
        if not isinstance(qhash, str) or len(qhash) != 64 or any(ch not in "0123456789abcdef" for ch in qhash):
            raise MatchResultError("Invalid question-set digest.")
        participants, teams, context = source.get("participants"), source.get("teams"), source.get("result_context")
        if not isinstance(participants, list) or not participants or len(participants) > 20 or not isinstance(teams, list) or len(teams) > 8 or not isinstance(context, dict):
            raise MatchResultError("Invalid match result structure.")
        minimum_rosters = {
            "classic": 2, "speed_challenge": 2, "team_battle": 4, "elimination": 2,
            "solo_practice": 1, "quiz_royale": 4, "power_play": 2, "bluff_master": 3, "escape_room": 2,
        }
        if len(participants) < minimum_rosters[str(source["mode"])]:
            raise MatchResultError("The match roster is smaller than the mode minimum.")
        maximum_roster = 20 if source["mode"] == "quiz_royale" else 8
        if len(participants) > maximum_roster or (source["mode"] == "team_battle" and len(participants) % 2):
            raise MatchResultError("The match roster exceeds the mode limit or team-size rule.")
        expected_kind = {
            "team_battle": "team", "escape_room": "cooperative", "solo_practice": "practice",
        }.get(str(source["mode"]), "individual")
        if source["result_kind"] != expected_kind:
            raise MatchResultError("The result kind does not match the selected game mode.")
        try:
            context_bytes = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise MatchResultError("Match result context must contain JSON-safe values.") from error
        if len(context_bytes) > 32_768:
            raise MatchResultError("Match result context exceeds its storage limit.")
        normalized_participants: list[dict[str, object]] = []
        seats: set[int] = set()
        player_ids: set[str] = set()
        for entry in participants:
            if not isinstance(entry, dict):
                raise MatchResultError("Invalid participant result.")
            seat, player_id, team_id = entry.get("seat_index"), entry.get("player_id"), entry.get("team_id")
            if isinstance(seat, bool) or not isinstance(seat, int) or seat < 0 or seat in seats:
                raise MatchResultError("Participant seats must be unique non-negative integers.")
            seats.add(seat)
            if player_id is not None and (not isinstance(player_id, str) or len(player_id) != 32 or player_id in player_ids):
                raise MatchResultError("Player identities must be unique valid profile IDs.")
            if player_id is not None:
                player_ids.add(player_id)
            if team_id is not None and (not isinstance(team_id, str) or len(team_id) > 24):
                raise MatchResultError("Invalid participant team ID.")
            normalized: dict[str, object] = {"seat_index": seat, "player_id": player_id, "team_id": team_id}
            for field in ("final_score", "correct_count", "answer_count", "placement"):
                value = entry.get(field)
                if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0 or (field == "placement" and value < 1)):
                    raise MatchResultError(f"Invalid participant {field}.")
                normalized[field] = value
            outcome = entry.get("outcome")
            allowed_outcomes = {"win", "loss", "draw", "team_win", "team_loss", "team_draw", "practice", "cooperative_success", "cooperative_failure", "participated"}
            if outcome not in allowed_outcomes:
                raise MatchResultError("Invalid participant outcome.")
            normalized["outcome"] = outcome
            for field in ("reconnect_expired", "forfeited"):
                if not isinstance(entry.get(field), bool):
                    raise MatchResultError(f"Invalid participant {field}.")
                normalized[field] = entry[field]
            disconnect_count = entry.get("disconnect_count")
            if isinstance(disconnect_count, bool) or not isinstance(disconnect_count, int) or disconnect_count < 0:
                raise MatchResultError("Invalid participant disconnect count.")
            normalized["disconnect_count"] = disconnect_count
            normalized_participants.append(normalized)
        if not player_ids:
            raise MatchResultError("At least one durable profile must be bound to the match.")
        if expected_kind == "team":
            if len(teams) != 2 or any(not row["team_id"] for row in normalized_participants):
                raise MatchResultError("Team matches need two team results and a team for each participant.")
            if any(row["placement"] is not None or row["outcome"] not in {"team_win", "team_loss", "team_draw"} for row in normalized_participants):
                raise MatchResultError("Team matches cannot assign individual placements.")
        elif expected_kind == "cooperative":
            if teams or any(row["placement"] is not None or row["final_score"] is not None or row["correct_count"] is not None or row["answer_count"] is not None for row in normalized_participants):
                raise MatchResultError("Cooperative matches do not have individual scores or placements.")
            if any(row["outcome"] not in {"cooperative_success", "cooperative_failure"} for row in normalized_participants):
                raise MatchResultError("Cooperative outcomes must be shared by the team.")
            expected_outcome = "cooperative_success" if context.get("outcome") == "victory" else "cooperative_failure" if context.get("outcome") == "failure" else None
            if expected_outcome is None or any(row["outcome"] != expected_outcome for row in normalized_participants):
                raise MatchResultError("Cooperative participant outcomes must match the team result.")
        elif expected_kind == "practice":
            if len(participants) != 1 or teams or normalized_participants[0]["placement"] is not None or normalized_participants[0]["outcome"] != "practice":
                raise MatchResultError("Solo Practice cannot create competitive placements or outcomes.")
        else:
            if teams or any(row["placement"] is None or row["outcome"] not in {"win", "loss", "draw"} for row in normalized_participants):
                raise MatchResultError("Individual competitive matches need placements and individual outcomes.")
            winners = sum(row["placement"] == 1 for row in normalized_participants)
            if winners < 1 or any(
                row["outcome"] != ("draw" if winners > 1 else "win") if row["placement"] == 1
                else row["outcome"] != "loss"
                for row in normalized_participants
            ):
                raise MatchResultError("Individual outcomes must agree with server placements.")
        normalized_teams: list[dict[str, object]] = []
        team_ids: set[str] = set()
        for team in teams:
            if not isinstance(team, dict) or not isinstance(team.get("team_id"), str) or team["team_id"] in team_ids:
                raise MatchResultError("Team result IDs must be unique.")
            team_ids.add(team["team_id"])
            row = {"team_id": team["team_id"]}
            for field in ("score", "correct_count", "response_time_ms"):
                value = team.get(field)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise MatchResultError(f"Invalid team {field}.")
                row[field] = value
            placement = team.get("placement")
            if placement is not None and (isinstance(placement, bool) or not isinstance(placement, int) or placement < 1):
                raise MatchResultError("Invalid team placement.")
            if team.get("outcome") not in {"win", "loss", "draw"}:
                raise MatchResultError("Invalid team outcome.")
            row.update({"placement": placement, "outcome": team["outcome"]})
            normalized_teams.append(row)
        if expected_kind == "team":
            team_winners = sum(team["placement"] == 1 for team in normalized_teams)
            if team_winners < 1 or any(
                team["outcome"] != ("draw" if team["placement"] == 1 and team_winners > 1 else "win" if team["placement"] == 1 else "loss")
                for team in normalized_teams
            ):
                raise MatchResultError("Team outcomes must agree with server team standings.")
            team_outcomes = {team["team_id"]: f"team_{team['outcome']}" for team in normalized_teams}
            if any(team_outcomes.get(row["team_id"]) != row["outcome"] for row in normalized_participants):
                raise MatchResultError("Team participant outcomes must match the official team result.")
        return {
            "source": "game_engine", "engine_match_id": source["engine_match_id"], "mode": source["mode"],
            "status": "completed", "result_kind": source["result_kind"], "rules_version": source["rules_version"],
            "started_at": started, "completed_at": completed, "question_set_hash": qhash,
            "participants": normalized_participants, "teams": normalized_teams, "result_context": context,
        }

    def list_matches(self, player_id: str, *, limit: int, offset: int, mode: str | None = None) -> dict[str, object]:
        with self._database.session() as session:
            statement = (
                select(CompetitiveMatch, MatchParticipant)
                .join(MatchParticipant, MatchParticipant.match_id == CompetitiveMatch.id)
                .where(MatchParticipant.player_id == player_id)
            )
            count_statement = select(func.count()).select_from(MatchParticipant).join(CompetitiveMatch).where(MatchParticipant.player_id == player_id)
            if mode:
                statement = statement.where(CompetitiveMatch.mode == mode)
                count_statement = count_statement.where(CompetitiveMatch.mode == mode)
            total = int(session.scalar(count_statement) or 0)
            rows = session.execute(statement.order_by(CompetitiveMatch.completed_at.desc(), CompetitiveMatch.id.desc()).limit(limit).offset(offset)).all()
            return {
                "history_available": total > 0,
                "total": total,
                "limit": limit,
                "offset": offset,
                "matches": [self._history_view(match, participant, session) for match, participant in rows],
            }

    def stats(self, player_id: str) -> dict[str, object]:
        with self._database.session() as session:
            rows = session.execute(
                select(CompetitiveMatch.mode, CompetitiveMatch.result_kind, MatchParticipant.outcome, func.count())
                .join(MatchParticipant, MatchParticipant.match_id == CompetitiveMatch.id)
                .where(MatchParticipant.player_id == player_id)
                .group_by(CompetitiveMatch.mode, CompetitiveMatch.result_kind, MatchParticipant.outcome)
            ).all()
        if not rows:
            return {"history_available": False, "completed_matches": 0, "wins": 0, "losses": 0, "draws": 0, "mode_breakdown": []}
        totals = {"completed_matches": 0, "wins": 0, "losses": 0, "draws": 0}
        modes: dict[str, dict[str, int | str]] = {}
        for mode, kind, outcome, count in rows:
            entry = modes.setdefault(mode, {"mode": mode, "matches": 0, "wins": 0, "losses": 0, "draws": 0, "other": 0})
            entry["matches"] = int(entry["matches"]) + count
            totals["completed_matches"] += count
            result = {"win": "wins", "team_win": "wins", "loss": "losses", "team_loss": "losses", "draw": "draws", "team_draw": "draws"}.get(outcome)
            if result and kind not in {"practice", "cooperative"}:
                entry[result] = int(entry[result]) + count
                totals[result] += count
            else:
                entry["other"] = int(entry["other"]) + count
        return {"history_available": True, **totals, "mode_breakdown": sorted(modes.values(), key=lambda item: str(item["mode"]))}

    @staticmethod
    def _history_view(match: CompetitiveMatch, own: MatchParticipant, session) -> dict[str, object]:
        team = None
        if own.team_id:
            record = session.scalar(select(MatchTeam).where(MatchTeam.match_id == match.id, MatchTeam.team_id == own.team_id))
            if record:
                team = {"team_id": record.team_id, "score": record.score, "correct_count": record.correct_count, "placement": record.placement, "outcome": record.outcome}
        return {
            "id": match.id, "mode": match.mode, "result_kind": match.result_kind,
            "started_at": match.started_at, "completed_at": match.completed_at,
            "final_score": own.final_score, "correct_count": own.correct_count,
            "answer_count": own.answer_count, "placement": own.placement,
            "outcome": own.outcome, "team": team,
            "disconnect_count": own.disconnect_count, "reconnect_expired": own.reconnect_expired,
            "forfeited": own.forfeited,
        }


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

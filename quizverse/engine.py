"""Authoritative, single-process multiplayer game state and transitions."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Callable

from quizverse.modes import CLASSIC_CONFIG, ModeConfig, TEAM_BATTLE_CONFIG, GameMode, resolve_mode
from quizverse.questions import QUESTIONS, Question
from quizverse.special_questions import BLUFF_QUESTIONS, ESCAPE_QUESTIONS
from quizverse.question_content import normalize_open_answer, open_answer_matches
from quizverse.preferences import PlayerPreferences
from quizverse.question_intelligence import SelectionFilters, select_questions
from quizverse.taxonomy import CATEGORIES

ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_CAPACITY = CLASSIC_CONFIG.max_players
QUESTION_COUNT = CLASSIC_CONFIG.question_count
QUESTION_SECONDS = CLASSIC_CONFIG.question_seconds
COUNTDOWN_SECONDS = CLASSIC_CONFIG.countdown_seconds
REVEAL_SECONDS = CLASSIC_CONFIG.reveal_seconds
RECONNECT_SECONDS = 60.0
ROOM_IDLE_SECONDS = 3600.0
MOMENTUM_GAP_POINTS = CLASSIC_CONFIG.momentum_gap_points
MOMENTUM_SPEED_SECONDS = CLASSIC_CONFIG.momentum_speed_seconds
MOMENTUM_AWARD_POINTS = CLASSIC_CONFIG.momentum_bonus_points
NAME_PATTERN = re.compile(r"^[\w .'-]{1,18}$", re.UNICODE)
TEAM_NAMES = {"violet": "Team Violet", "cyan": "Team Cyan"}


class Phase(StrEnum):
    WAITING = "waiting"
    COUNTDOWN = "countdown"
    QUESTION = "question"
    REVEAL = "reveal"
    RESULTS = "results"
    SUBMISSION = "submission"
    VOTING = "voting"
    ESCAPE_PUZZLE = "escape_puzzle"


class GameError(Exception):
    """Expected, safe-to-display game rule or validation error."""

    def __init__(self, code: str, message: str, *, state_changed: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        # A late answer can discover and apply a deadline transition before the
        # scheduler tick. The transport must then broadcast the new room state.
        self.state_changed = state_changed


@dataclass(slots=True)
class Answer:
    choice: int
    accepted_at: float
    speed_bonus: int = 0
    momentum_bonus: int = 0
    doubled: bool = False
    shield_consumed: bool = False
    power_penalty: bool = False


@dataclass(slots=True)
class Player:
    id: str
    name: str
    token_hash: str
    connected: bool = True
    score: int = 0
    correct: int = 0
    answers_submitted: int = 0
    momentum_points: int = 0
    correct_response_time_ms: int = 0
    team_id: str | None = None
    answer: Answer | None = None
    disconnected_at: float | None = None
    reconnect_expired: bool = False
    eliminated: bool = False
    eliminated_round: int | None = None
    elimination_reason: str | None = None
    lives: int = 0
    power_energy: int = 0
    power_used: set[str] = field(default_factory=set)
    power_requests: set[str] = field(default_factory=set)
    power_double_active: bool = False
    power_shield_active: bool = False
    power_fifty_round: int | None = None
    power_fifty_choices: tuple[int, ...] = ()
    power_round_used: bool = False


@dataclass(slots=True)
class Room:
    code: str
    host_id: str
    created_at: float
    last_activity: float
    match_config: ModeConfig = CLASSIC_CONFIG
    players: dict[str, Player] = field(default_factory=dict)
    phase: Phase = Phase.WAITING
    questions: tuple[Question, ...] = ()
    round_index: int = 0
    deadline: float | None = None
    deadline_epoch_ms: int | None = None
    question_started_at: float | None = None
    momentum_eligible_ids: set[str] = field(default_factory=set)
    momentum_adjustments: list[dict[str, object]] = field(default_factory=list)
    team_assignment_roster: tuple[str, ...] = ()
    elimination_end_reason: str | None = None
    terminal_elimination_round: int | None = None
    selection: dict[str, object] = field(default_factory=dict)
    bluff_submissions: dict[str, str] = field(default_factory=dict)
    bluff_options: list[dict[str, object]] = field(default_factory=list)
    bluff_votes: dict[str, str] = field(default_factory=dict)
    bluff_request_ids: set[str] = field(default_factory=set)
    escape_hints_remaining: int = 3
    escape_active_hint: str | None = None
    escape_request_ids: set[str] = field(default_factory=set)
    escape_hinted_puzzles: set[int] = field(default_factory=set)
    escape_attempts: dict[str, tuple[int, float]] = field(default_factory=dict)
    escape_started_at: float | None = None
    escape_completed_at: float | None = None


class GameEngine:
    """Serializes mutations so every event and timer observes atomic state."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        question_bank: tuple[Question, ...] = QUESTIONS,
        question_provider: Callable[[dict[str, object]], tuple[Question, ...]] | None = None,
    ) -> None:
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._question_bank = question_bank
        self._question_provider = question_provider
        self._rooms: dict[str, Room] = {}
        self._sessions: dict[str, tuple[str, str]] = {}
        self._lock = asyncio.Lock()

    def set_question_provider(self, provider: Callable[[dict[str, object]], tuple[Question, ...]]) -> None:
        """Install the authorization-aware content source used by this app instance."""
        self._question_provider = provider

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def validate_name(value: object) -> str:
        if not isinstance(value, str):
            raise GameError("invalid_name", "Enter a display name using 1–18 letters or numbers.")
        name = value.strip()
        if not NAME_PATTERN.fullmatch(name):
            raise GameError("invalid_name", "Use 1–18 letters or numbers; spaces, apostrophes, dots, and hyphens are allowed.")
        return name

    def _new_code(self) -> str:
        for _ in range(100):
            code = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(6))
            if code not in self._rooms:
                return code
        raise GameError("room_unavailable", "A room code could not be allocated. Please try again.")

    def _set_deadline(self, room: Room, seconds: float) -> None:
        self._set_deadline_at(room, self._monotonic() + seconds)

    def _set_deadline_at(self, room: Room, deadline: float) -> None:
        remaining = deadline - self._monotonic()
        room.deadline = deadline
        room.deadline_epoch_ms = int((self._wall_time() + remaining) * 1000)

    def _touch(self, room: Room) -> None:
        room.last_activity = self._monotonic()

    def _get_room_player(self, room_code: str, player_id: str) -> tuple[Room, Player]:
        room = self._rooms.get(room_code)
        player = room.players.get(player_id) if room else None
        if room is None or player is None:
            raise GameError("session_expired", "This room session has expired. Join the room again.")
        return room, player

    def _team_assignments(self, room: Room) -> dict[str, str]:
        if room.match_config.mode != GameMode.TEAM_BATTLE:
            return {}
        roster = tuple(room.players)
        if room.phase != Phase.WAITING or room.team_assignment_roster == roster:
            assigned = {
                player_id: player.team_id
                for player_id, player in room.players.items()
                if player.team_id in TEAM_NAMES
            }
            if len(assigned) == len(room.players):
                return assigned  # type: ignore[return-value]
            if room.phase != Phase.WAITING:
                return assigned  # preserve frozen memberships after cleanup
        return {
            player_id: "violet" if index % 2 == 0 else "cyan"
            for index, player_id in enumerate(roster)
        }

    def _public_player(
        self,
        room: Room,
        player: Player,
        *,
        team_id: str | None = None,
    ) -> dict[str, object]:
        public = {
            "id": player.id,
            "name": player.name,
            "connected": player.connected,
            "reconnect_expired": player.reconnect_expired,
            "score": player.score,
            "correct": player.correct,
            "momentum_points": player.momentum_points,
            "answered": player.answer is not None and player.answer.choice >= 0,
            "is_host": player.id == room.host_id,
        }
        if room.match_config.mode == GameMode.TEAM_BATTLE:
            team_id = team_id or player.team_id
            public["team_id"] = team_id
            public["team_name"] = TEAM_NAMES.get(team_id, "Team")
            public["correct_response_time_ms"] = player.correct_response_time_ms
        if room.match_config.elimination:
            public["eliminated"] = player.eliminated
            public["eliminated_round"] = player.eliminated_round
            public["elimination_reason"] = player.elimination_reason
            public["correct_response_time_ms"] = player.correct_response_time_ms
            public["survival_round"] = (
                max(0, player.eliminated_round - 1)
                if player.eliminated_round is not None
                else room.round_index + (1 if room.phase == Phase.RESULTS else 0)
            )
            if room.match_config.royale:
                public["lives"] = player.lives
        return public

    @staticmethod
    def _power_snapshot(room: Room, player: Player) -> dict[str, object]:
        abilities = ("fifty_fifty", "double_points", "shield")
        return {
            "energy": player.power_energy,
            "max_energy": 5,
            "cost": 2,
            "available": [name for name in abilities if name not in player.power_used],
            "used": sorted(player.power_used),
            "double_active": player.power_double_active,
            "shield_active": player.power_shield_active,
            "used_this_round": player.power_round_used,
        }

    def _elimination_results(self, room: Room) -> tuple[list[dict[str, object]], dict[str, object]]:
        players = list(room.players.values())
        terminal_ids = {
            player.id
            for player in players
            if room.terminal_elimination_round is not None
            and player.eliminated_round == room.terminal_elimination_round
        }

        def metrics(player: Player) -> tuple[int, int, int]:
            if room.match_config.royale:
                return player.lives, player.score, player.correct
            return player.score, player.correct, player.correct_response_time_ms

        def sort_key(player: Player) -> tuple[object, ...]:
            if room.match_config.royale:
                if terminal_ids:
                    return (0, player.name.casefold()) if player.id in terminal_ids else (1, -player.lives, -player.score, -player.correct, player.name.casefold())
                return (0, -player.lives, -player.score, -player.correct, player.name.casefold()) if not player.eliminated else (1, -(player.eliminated_round or 0), -player.lives, -player.score, player.name.casefold())
            if terminal_ids:
                if player.id in terminal_ids:
                    return (0, 0, 0, 0, 0, player.name.casefold())
                return (
                    1,
                    -(player.eliminated_round or 0),
                    -player.score,
                    -player.correct,
                    player.correct_response_time_ms,
                    player.name.casefold(),
                )
            if not player.eliminated:
                return (0, 0, -player.score, -player.correct, player.correct_response_time_ms, player.name.casefold())
            return (
                1,
                -(player.eliminated_round or 0),
                -player.score,
                -player.correct,
                player.correct_response_time_ms,
                player.name.casefold(),
            )

        ordered = sorted(players, key=sort_key)
        placements: dict[str, int] = {}
        shared_ids: set[str] = set()
        if terminal_ids:
            for player_id in terminal_ids:
                placements[player_id] = 1
            if len(terminal_ids) > 1:
                shared_ids.update(terminal_ids)
            place = len(terminal_ids) + 1
            previous_key: tuple[object, ...] | None = None
            previous_place = place
            for player in ordered:
                if player.id in terminal_ids:
                    continue
                key = (player.eliminated_round, *metrics(player))
                if previous_key is not None and key == previous_key:
                    placements[player.id] = previous_place
                    shared_ids.update(
                        candidate.id
                        for candidate in ordered
                        if candidate.id not in terminal_ids
                        and (candidate.eliminated_round, *metrics(candidate)) == key
                    )
                else:
                    previous_place = place
                    placements[player.id] = place
                previous_key = key
                place += 1
        else:
            previous_key = None
            previous_place = 0
            for index, player in enumerate(ordered, start=1):
                key = (
                    not player.eliminated,
                    player.eliminated_round if player.eliminated else None,
                    *metrics(player),
                )
                if key == previous_key:
                    placements[player.id] = previous_place
                    shared_ids.add(player.id)
                    shared_ids.update(
                        candidate.id
                        for candidate in ordered
                        if candidate.id != player.id
                        and (
                            not candidate.eliminated,
                            candidate.eliminated_round if candidate.eliminated else None,
                            *metrics(candidate),
                        ) == key
                    )
                else:
                    previous_place = index
                    placements[player.id] = index
                previous_key = key

        result_rows = [
            {
                **self._public_player(room, player),
                "placement": placements[player.id],
                "shared_placement": player.id in shared_ids,
            }
            for player in ordered
        ]
        survivors = [player for player in ordered if not player.eliminated]
        if terminal_ids:
            winners: list[Player] = []
            is_draw = True
            draw_reason = "all_eliminated"
        elif survivors:
            top_key = metrics(survivors[0])
            winners = [player for player in survivors if metrics(player) == top_key]
            is_draw = len(winners) > 1
            draw_reason = "equal_survivor_results" if is_draw else None
        else:
            winners = []
            is_draw = False
            draw_reason = None

        shared_placements: dict[int, list[str]] = {}
        for player_id in shared_ids:
            shared_placements.setdefault(placements[player_id], []).append(player_id)
        summary: dict[str, object] = {
            "remaining_players": len(survivors),
            "winning_player_ids": [player.id for player in winners],
            "is_draw": is_draw,
            "draw_reason": draw_reason,
            "end_reason": room.elimination_end_reason or "question_limit",
            "shared_placements": [
                {"placement": place, "player_ids": sorted(player_ids)}
                for place, player_ids in sorted(shared_placements.items())
            ],
        }
        return result_rows, summary

    def _team_standings(
        self,
        room: Room,
        assignments: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        assignments = assignments if assignments is not None else self._team_assignments(room)
        totals = {
            team_id: {"team_id": team_id, "name": name, "score": 0, "correct": 0, "correct_response_time_ms": 0}
            for team_id, name in TEAM_NAMES.items()
        }
        for player_id, player in room.players.items():
            team_id = assignments.get(player_id)
            if team_id not in totals:
                continue
            team = totals[team_id]
            team["score"] += player.score
            team["correct"] += player.correct
            team["correct_response_time_ms"] += player.correct_response_time_ms

        standings = sorted(
            totals.values(),
            key=lambda team: (
                -int(team["score"]),
                -int(team["correct"]),
                int(team["correct_response_time_ms"]),
            ),
        )
        previous_key: tuple[int, int, int] | None = None
        previous_place = 0
        for index, team in enumerate(standings, start=1):
            key = (
                int(team["score"]),
                int(team["correct"]),
                int(team["correct_response_time_ms"]),
            )
            place = previous_place if key == previous_key else index
            team["placement"] = place if room.phase == Phase.RESULTS else None
            previous_key = key
            previous_place = place

        if room.phase == Phase.RESULTS:
            best = (
                int(standings[0]["score"]),
                int(standings[0]["correct"]),
                int(standings[0]["correct_response_time_ms"]),
            )
            winners = [
                team for team in standings
                if (
                    int(team["score"]),
                    int(team["correct"]),
                    int(team["correct_response_time_ms"]),
                ) == best
            ]
            winning_ids = [str(team["team_id"]) for team in winners]
            for team in standings:
                team["is_winner"] = team["team_id"] in winning_ids
                team["shared_placement"] = len(winners) > 1 and team["is_winner"]
        return standings

    def _practice_summary(self, room: Room, player: Player) -> dict[str, object]:
        attempted = player.answers_submitted
        correct = player.correct
        return {
            "score": player.score,
            "correct_answers": correct,
            "incorrect_answers": attempted - correct,
            "unanswered_questions": room.match_config.question_count - attempted,
            "attempted_questions": attempted,
            "total_questions": room.match_config.question_count,
            "accuracy_percent": round(correct / attempted * 100, 1) if attempted else None,
            "correct_response_time_ms": player.correct_response_time_ms,
        }

    def _can_start(self, room: Room) -> bool:
        connected = sum(player.connected for player in room.players.values())
        if room.match_config.mode == GameMode.SOLO_PRACTICE:
            return len(room.players) == 1 and connected == 1
        if connected < room.match_config.min_players:
            return False
        if room.match_config.even_player_count:
            now = self._monotonic()
            roster_size = sum(
                player.connected
                or player.disconnected_at is None
                or now - player.disconnected_at <= RECONNECT_SECONDS
                for player in room.players.values()
            )
            return 4 <= roster_size <= room.match_config.max_players and roster_size % 2 == 0
        return True

    def _can_start_reason(self, room: Room) -> str | None:
        if room.match_config.royale and sum(player.connected for player in room.players.values()) < 4:
            return "Quiz Royale needs at least four connected players."
        if room.match_config.mode == GameMode.SOLO_PRACTICE and (len(room.players) != 1 or not any(player.connected for player in room.players.values())):
            return "Solo Practice needs exactly one connected player."
        if room.match_config.bluff_master and sum(player.connected for player in room.players.values()) < 3:
            return "Bluff Master needs at least three connected players."
        if room.match_config.escape_room and sum(player.connected for player in room.players.values()) < 2:
            return "Escape Room needs at least two connected players."
        if room.match_config.mode == GameMode.TEAM_BATTLE:
            now = self._monotonic()
            roster_size = sum(
                player.connected
                or player.disconnected_at is None
                or now - player.disconnected_at <= RECONNECT_SECONDS
                for player in room.players.values()
            )
            if roster_size < 4 or roster_size > room.match_config.max_players or roster_size % 2:
                return "Team Battle needs an even roster of 4, 6, or 8 real players."
            if sum(player.connected for player in room.players.values()) < room.match_config.min_players:
                return "At least four players must be connected to start Team Battle."
        elif sum(player.connected for player in room.players.values()) < room.match_config.min_players:
            if room.match_config.elimination:
                return "At least two players must be connected to start Elimination."
            return "Waiting for at least one more connected player."
        return None

    @staticmethod
    def _valid_escape_set(candidates: list[Question]) -> bool:
        if len(candidates) != 10:
            return False
        by_id = {question.id: question for question in candidates}
        if len(by_id) != 10:
            return False
        payloads = [question.format_payload for question in candidates]
        if any(not isinstance(payload, dict) for payload in payloads):
            return False
        indexes = [payload.get("puzzle_index") for payload in payloads if isinstance(payload, dict)]
        if set(indexes) != set(range(10)):
            return False
        for question in candidates:
            payload = question.format_payload or {}
            index = payload.get("puzzle_index")
            room_index = payload.get("room_index")
            if room_index != (index // 2 + 1) or not str(payload.get("solution", "")).strip():
                return False
            dependencies = payload.get("dependencies", [])
            if not isinstance(dependencies, list) or any(dependency not in by_id or dependency == question.id for dependency in dependencies):
                return False
            if any(
                int((by_id[dependency].format_payload or {}).get("puzzle_index", 99)) >= int(index)
                for dependency in dependencies
            ):
                return False
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(question_id: str) -> bool:
            if question_id in visiting:
                return False
            if question_id in visited:
                return True
            visiting.add(question_id)
            payload = by_id[question_id].format_payload or {}
            for dependency in payload.get("dependencies", []):
                if not visit(dependency):
                    return False
            visiting.remove(question_id)
            visited.add(question_id)
            return True

        return all(visit(question_id) for question_id in by_id)

    @classmethod
    def _select_escape_puzzle_set(cls, inventory: tuple[Question, ...], filters: SelectionFilters) -> tuple[Question, ...]:
        groups: dict[str, list[Question]] = {}
        for question in inventory:
            payload = question.format_payload or {}
            set_id = payload.get("puzzle_set_id")
            if question.question_format == "puzzle" and isinstance(set_id, str):
                groups.setdefault(set_id, []).append(question)
        valid_groups = [group for group in groups.values() if cls._valid_escape_set(group)]
        if not valid_groups:
            raise ValueError("No complete reviewed Escape Room puzzle set is available (five rooms and ten linked puzzles are required).")
        errors: list[str] = []
        for group in valid_groups:
            try:
                selected = select_questions(group, filters)
            except (ValueError, TypeError) as error:
                errors.append(str(error))
                continue
            ordered = tuple(sorted(selected, key=lambda item: int((item.format_payload or {}).get("puzzle_index", -1))))
            return ordered
        detail = errors[0] if errors else "No complete puzzle set matches the selected filters."
        raise ValueError(detail)

    def _snapshot(self, room: Room, viewer_id: str) -> dict[str, object]:
        viewer = room.players.get(viewer_id)
        question = room.questions[room.round_index] if room.round_index < len(room.questions) else None
        players = sorted(room.players.values(), key=lambda p: (-p.score, -p.correct, p.name.casefold()))
        public_question = None
        if room.phase in (Phase.QUESTION, Phase.REVEAL) and question and question.question_format == "multiple_choice":
            public_question = {"category": question.category, "prompt": question.prompt, "options": list(question.options)}
            if room.match_config.power_play and room.phase == Phase.QUESTION and viewer and viewer.power_fifty_round == room.round_index:
                public_question["visible_choices"] = list(viewer.power_fifty_choices)
                public_question["options"] = [option if index in viewer.power_fifty_choices else "" for index, option in enumerate(question.options)]
        if room.match_config.bluff_master and room.phase in {Phase.SUBMISSION, Phase.VOTING, Phase.REVEAL} and question:
            public_question = {"category": question.category, "prompt": question.prompt, "format": "open_ended"}
        if room.match_config.escape_room and room.phase == Phase.ESCAPE_PUZZLE and question:
            public_question = {"category": question.category, "prompt": question.prompt, "format": "puzzle"}
        feedback = None
        if room.phase == Phase.REVEAL and viewer and question and question.question_format == "multiple_choice":
            feedback = {
                "selected": viewer.answer.choice if viewer.answer and viewer.answer.choice >= 0 else None,
                "correct": question.answer,
                "was_correct": bool(viewer.answer and viewer.answer.choice == question.answer),
                "speed_bonus": viewer.answer.speed_bonus if viewer.answer else 0,
                "momentum_bonus": viewer.answer.momentum_bonus if viewer.answer else 0,
                "doubled": bool(viewer.answer and viewer.answer.doubled),
                "shield_consumed": bool(viewer.answer and viewer.answer.shield_consumed),
                "power_penalty": bool(viewer.answer and viewer.answer.power_penalty),
            }
        team_assignments = self._team_assignments(room)
        public_players = [
            self._public_player(room, player, team_id=team_assignments.get(player.id))
            for player in players
        ]
        teams = self._team_standings(room, team_assignments) if room.match_config.mode == GameMode.TEAM_BATTLE else None
        elimination_summary = None
        if room.match_config.elimination and room.phase == Phase.RESULTS:
            public_players, elimination_summary = self._elimination_results(room)
        result_data = None
        if room.phase == Phase.RESULTS:
            if room.match_config.escape_room:
                outcome = "victory" if room.round_index >= 10 else "failure"
                result_data = self._escape_results(room, outcome)
            elif room.match_config.mode == GameMode.SOLO_PRACTICE and viewer is not None:
                result_data = {"practice_summary": self._practice_summary(room, viewer)}
            else:
                result_data = {
                    "leaderboard": public_players,
                    "momentum_adjustments": list(room.momentum_adjustments),
                }
            if teams is not None:
                winning_ids = [str(team["team_id"]) for team in teams if team.get("is_winner")]
                result_data.update(
                    {
                        "teams": teams,
                        "winning_team_ids": winning_ids,
                        "is_draw": len(winning_ids) > 1,
                    }
                )
            if elimination_summary is not None:
                result_data.update(elimination_summary)
        can_start = room.phase == Phase.WAITING and room.host_id == viewer_id and self._can_start(room)
        return {
            "code": room.code,
            "mode": room.match_config.mode.value,
            "match_filters": {
                "content_scope": room.selection.get("content_scope", "built_in"),
                "difficulty": room.selection.get("difficulty", "medium"),
                "language": room.selection.get("language", "en"),
                "learning_preset": room.selection.get("learning_preset", "teen_challenge"),
                "categories": list(room.selection.get("categories", [])),
                "question_count": room.match_config.question_count,
                "exam_collection_id": room.selection.get("exam_collection_id"),
            },
            "phase": room.phase.value,
            "host_id": room.host_id,
            "players": public_players,
            **({"remaining_players": sum(not player.eliminated for player in room.players.values())} if room.match_config.elimination else {}),
            **({"teams": teams} if teams is not None else {}),
            "round": room.round_index + 1,
            "total_rounds": room.match_config.question_count,
            "question_seconds": room.match_config.question_seconds,
            "question": public_question,
            "deadline": room.deadline_epoch_ms,
            "server_now": int(self._wall_time() * 1000),
            "answered": bool(viewer and viewer.answer is not None and viewer.answer.choice >= 0),
            **({"max_players": room.match_config.max_players} if room.match_config.royale else {}),
            **({"power_play": self._power_snapshot(room, viewer)} if room.match_config.power_play and viewer else {}),
            **({"bluff": self._bluff_snapshot(room, viewer)} if room.match_config.bluff_master else {}),
            **({"escape": self._escape_snapshot(room)} if room.match_config.escape_room else {}),
            "answer_feedback": feedback,
            "momentum": {
                "enabled": room.match_config.momentum_enabled,
                "eligible": viewer_id in room.momentum_eligible_ids,
                "bonus_points": room.match_config.momentum_bonus_points,
                "gap_points": room.match_config.momentum_gap_points,
                "speed_seconds": room.match_config.momentum_speed_seconds,
            },
            "results": (
                {
                    **result_data,
                }
                if result_data is not None else None
            ),
            "can_start": can_start,
            **({"can_start_reason": self._can_start_reason(room)} if room.match_config.mode in {GameMode.TEAM_BATTLE, GameMode.SOLO_PRACTICE} or room.match_config.elimination or room.match_config.bluff_master or room.match_config.escape_room else {}),
        }

    def _bluff_snapshot(self, room: Room, viewer: Player | None) -> dict[str, object]:
        data: dict[str, object] = {
            "submitted": bool(viewer and viewer.id in room.bluff_submissions),
            "submitted_count": len(room.bluff_submissions),
            "player_count": len(room.players),
            "voted": bool(viewer and viewer.id in room.bluff_votes),
        }
        if viewer and room.phase == Phase.SUBMISSION and viewer.id in room.bluff_submissions:
            data["your_submission"] = room.bluff_submissions[viewer.id]
        if room.phase == Phase.VOTING:
            data["choices"] = [
                {"id": item["id"], "text": item["text"], "is_yours": bool(viewer and item.get("author_id") == viewer.id)}
                for item in room.bluff_options
            ]
        if room.phase == Phase.REVEAL:
            question = room.questions[room.round_index]
            payload = question.format_payload or {}
            data["correct_answer"] = payload.get("canonical_answer")
            data["results"] = [
                {
                    "text": option["text"],
                    "correct": bool(option["is_correct"]),
                    "author": "Correct answer" if option["is_correct"] else room.players[str(option["author_id"])].name,
                    "fooled_players": [
                        room.players[voter_id].name
                        for voter_id, selected_id in room.bluff_votes.items()
                        if selected_id == option["id"]
                    ],
                }
                for option in room.bluff_options
            ]
        return data

    def _escape_snapshot(self, room: Room) -> dict[str, object]:
        solved = min(room.round_index, 10)
        current = room.questions[room.round_index] if room.phase == Phase.ESCAPE_PUZZLE and room.round_index < 10 else None
        rooms = [
            {"room": index, "state": "solved" if solved >= index * 2 else "current" if solved >= (index - 1) * 2 and solved < index * 2 else "locked"}
            for index in range(1, 6)
        ]
        puzzle = None
        if current is not None:
            payload = current.format_payload or {}
            puzzle = {
                "index": room.round_index,
                "room": int(payload.get("room_index", room.round_index // 2 + 1)),
                "prompt": current.prompt,
                "hint": room.escape_active_hint,
            }
        return {
            "rooms": rooms,
            "completed_rooms": solved // 2,
            "solved_puzzles": solved,
            "total_puzzles": 10,
            "hints_remaining": room.escape_hints_remaining,
            "hint_used": room.escape_active_hint is not None,
            "current_puzzle": puzzle,
        }

    def _joined(self, room: Room, player: Player, token: str) -> dict[str, object]:
        return {"event": "joined", "token": token, "player_id": player.id, "state": self._snapshot(room, player.id)}

    async def create_room(self, raw_name: object, mode: object = "classic", selection: dict[str, object] | None = None) -> dict[str, object]:
        name = self.validate_name(raw_name)
        try:
            match_config = resolve_mode(mode)
        except ValueError as error:
            raise GameError("invalid_mode", str(error)) from error
        async with self._lock:
            now = self._monotonic()
            code = self._new_code()
            player_id = secrets.token_urlsafe(16)
            token = secrets.token_urlsafe(32)
            chosen = dict(selection or {})
            question_count = chosen.get("question_count", match_config.question_count)
            if question_count is None:
                question_count = match_config.question_count
            if isinstance(question_count, bool) or not isinstance(question_count, int) or not 1 <= question_count <= 20:
                raise GameError("invalid_question_count", "Question count must be between 1 and 20.")
            if (match_config.bluff_master or match_config.escape_room) and question_count != 10:
                raise GameError("invalid_question_count", "Bluff Master and Escape Room use exactly ten rounds or puzzles.")
            configured = match_config if question_count == match_config.question_count else replace(match_config, question_count=question_count)
            room = Room(
                code=code,
                host_id=player_id,
                created_at=now,
                last_activity=now,
                match_config=configured,
                selection=chosen,
            )
            player = Player(id=player_id, name=name, token_hash=self._token_hash(token))
            room.players[player_id] = player
            self._rooms[code] = room
            self._sessions[player.token_hash] = (code, player_id)
            return self._joined(room, player, token)

    async def join_room(self, raw_code: object, raw_name: object) -> dict[str, object]:
        name = self.validate_name(raw_name)
        if not isinstance(raw_code, str) or not re.fullmatch(r"[A-HJ-NP-Z2-9]{6}", raw_code.strip().upper()):
            raise GameError("invalid_code", "Room codes use six letters or numbers, excluding ambiguous characters.")
        code = raw_code.strip().upper()
        async with self._lock:
            room = self._rooms.get(code)
            if room is None:
                raise GameError("room_not_found", "Room not found. Check the code or create a new arena.")
            if room.phase != Phase.WAITING:
                raise GameError("game_started", "This game has already started.")
            if len(room.players) >= room.match_config.max_players:
                limit_label = "player" if room.match_config.max_players == 1 else "players"
                raise GameError("room_full", f"This arena is full ({room.match_config.max_players} {limit_label} maximum).")
            if any(player.name.casefold() == name.casefold() for player in room.players.values()):
                raise GameError("name_in_use", "That display name is already in this arena.")
            player_id = secrets.token_urlsafe(16)
            token = secrets.token_urlsafe(32)
            player = Player(id=player_id, name=name, token_hash=self._token_hash(token))
            room.players[player_id] = player
            if room.host_id not in room.players or not room.players[room.host_id].connected:
                room.host_id = player_id
            self._sessions[player.token_hash] = (code, player_id)
            self._touch(room)
            return self._joined(room, player, token)

    async def authenticate(self, raw_token: object) -> tuple[str, str]:
        if not isinstance(raw_token, str) or len(raw_token) > 128:
            raise GameError("invalid_session", "Your session token is invalid or expired.")
        async with self._lock:
            identity = self._sessions.get(self._token_hash(raw_token))
            if not identity:
                raise GameError("invalid_session", "Your session token is invalid or expired.")
            room, player = self._get_room_player(*identity)
            if not player.connected and player.disconnected_at is not None and self._monotonic() - player.disconnected_at > RECONNECT_SECONDS:
                raise GameError("session_expired", "Your reconnect window has expired. Join a new room.")
            return identity

    async def reconnect(self, raw_token: object) -> dict[str, object]:
        if not isinstance(raw_token, str) or len(raw_token) > 128:
            raise GameError("invalid_session", "Your session token is invalid or expired.")
        async with self._lock:
            identity = self._sessions.get(self._token_hash(raw_token))
            if not identity:
                raise GameError("invalid_session", "Your session token is invalid or expired.")
            room, player = self._get_room_player(*identity)
            if not player.connected and player.disconnected_at is not None and self._monotonic() - player.disconnected_at > RECONNECT_SECONDS:
                raise GameError("session_expired", "Your reconnect window has expired. Join a new room.")
            player.connected = True
            player.disconnected_at = None
            self._touch(room)
            return self._joined(room, player, raw_token)

    async def start_game(self, room_code: str, player_id: str) -> dict[str, object]:
        async with self._lock:
            room, _ = self._get_room_player(room_code, player_id)
            if room.host_id != player_id:
                raise GameError("host_only", "Only the host can start the quiz.")
            if room.phase != Phase.WAITING:
                raise GameError("wrong_phase", "The arena is not waiting to start.")
            if room.match_config.mode == GameMode.SOLO_PRACTICE:
                if len(room.players) != 1 or sum(player.connected for player in room.players.values()) != 1:
                    raise GameError("invalid_solo_roster", "Solo Practice needs exactly one connected player.")
            elif room.match_config.even_player_count:
                self._prune_expired_players(room, self._monotonic())
                if len(room.players) < 4 or len(room.players) > room.match_config.max_players or len(room.players) % 2:
                    raise GameError("invalid_team_roster", "Team Battle needs an even roster of 4, 6, or 8 real players.")
                if sum(player.connected for player in room.players.values()) < room.match_config.min_players:
                    raise GameError("not_enough_players", "At least four players must be connected to start Team Battle.")
            elif sum(player.connected for player in room.players.values()) < room.match_config.min_players:
                if room.match_config.royale:
                    message = "At least four connected players are needed to start Quiz Royale."
                elif room.match_config.bluff_master:
                    message = "At least three connected players are needed to start Bluff Master."
                elif room.match_config.escape_room:
                    message = "At least two connected players are needed to start Escape Room."
                else:
                    message = "At least two connected players are needed to start."
                raise GameError("not_enough_players", message)
            if room.match_config.mode == GameMode.TEAM_BATTLE:
                roster = tuple(room.players)
                if roster != room.team_assignment_roster or any(player.team_id not in TEAM_NAMES for player in room.players.values()):
                    for index, player in enumerate(room.players.values()):
                        player.team_id = "violet" if index % 2 == 0 else "cyan"
                    room.team_assignment_roster = roster
            try:
                parent_by_id = {f"category:{item.slug}": f"category:{item.parent_slug}" if item.parent_slug else None for item in CATEGORIES}
                preferences = PlayerPreferences.parse({
                    "learning_preset": room.selection.get("learning_preset", "teen_challenge"),
                    "difficulty": room.selection.get("difficulty", "medium"),
                    "language": room.selection.get("language", "en"),
                    "topics": room.selection.get("topic_preferences", {}),
                })
                source = str(room.selection.get("content_scope", "built_in"))
                if source == "built_in" and self._question_provider is None:
                    inventory = self._question_bank + BLUFF_QUESTIONS + ESCAPE_QUESTIONS
                elif self._question_provider is not None:
                    inventory = self._question_provider({**room.selection, "mode": room.match_config.mode.value})
                else:
                    raise ValueError("Quiz Library selection is unavailable in this game server.")
                selection_filters = SelectionFilters(
                        count=room.match_config.question_count,
                        difficulty=preferences.difficulty,
                        language=preferences.language,
                        learning_preset=preferences.learning_preset,
                        categories=frozenset(room.selection.get("categories", [])),
                        exam_collection_id=room.selection.get("exam_collection_id") if isinstance(room.selection.get("exam_collection_id"), str) else None,
                        question_format=("open_ended" if room.match_config.bluff_master else "puzzle" if room.match_config.escape_room else "multiple_choice"),
                        enforce_reviewed=room.match_config.bluff_master or room.match_config.escape_room,
                        parent_by_id=parent_by_id,
                        topic_preferences=preferences.topics,
                    )
                if room.match_config.escape_room:
                    room.questions = self._select_escape_puzzle_set(inventory, selection_filters)
                else:
                    room.questions = tuple(select_questions(inventory, selection_filters))
            except PermissionError as error:
                raise GameError("content_not_authorized", str(error)) from error
            except (ValueError, TypeError) as error:
                raise GameError("insufficient_inventory", str(error)) from error
            room.round_index = 0
            room.bluff_submissions.clear()
            room.bluff_options.clear()
            room.bluff_votes.clear()
            room.bluff_request_ids.clear()
            room.escape_hints_remaining = 3
            room.escape_active_hint = None
            room.escape_request_ids.clear()
            room.escape_hinted_puzzles.clear()
            room.escape_attempts.clear()
            room.escape_started_at = None
            room.escape_completed_at = None
            for player in room.players.values():
                player.score = 0
                player.correct = 0
                player.answers_submitted = 0
                player.momentum_points = 0
                player.correct_response_time_ms = 0
                player.eliminated = False
                player.eliminated_round = None
                player.elimination_reason = None
                player.answer = None
                player.lives = room.match_config.starting_lives
                player.power_energy = 3 if room.match_config.power_play else 0
                player.power_used.clear()
                player.power_requests.clear()
                player.power_double_active = False
                player.power_shield_active = False
                player.power_fifty_round = None
                player.power_fifty_choices = ()
                player.power_round_used = False
            room.momentum_eligible_ids.clear()
            room.momentum_adjustments.clear()
            room.elimination_end_reason = None
            room.terminal_elimination_round = None
            room.phase = Phase.COUNTDOWN
            self._set_deadline(room, room.match_config.countdown_seconds)
            self._touch(room)
            return self._snapshot(room, player_id)

    def _show_question(self, room: Room, *, started_at: float | None = None) -> None:
        if room.match_config.bluff_master:
            room.phase = Phase.SUBMISSION
            room.question_started_at = self._monotonic() if started_at is None else started_at
            room.bluff_submissions.clear()
            room.bluff_options.clear()
            room.bluff_votes.clear()
            self._set_deadline_at(room, room.question_started_at + 30.0)
            return
        if room.match_config.escape_room:
            room.phase = Phase.ESCAPE_PUZZLE
            if room.escape_started_at is None:
                room.escape_started_at = self._monotonic() if started_at is None else started_at
            room.question_started_at = room.escape_started_at
            self._set_deadline_at(room, room.escape_started_at + 900.0)
            return
        room.phase = Phase.QUESTION
        room.question_started_at = self._monotonic() if started_at is None else started_at
        for player in room.players.values():
            player.answer = None
            player.power_round_used = False
        leader_score = max((player.score for player in room.players.values()), default=0)
        if room.match_config.momentum_enabled:
            room.momentum_eligible_ids = {
                player.id
                for player in room.players.values()
                if 0 < leader_score - player.score <= room.match_config.momentum_gap_points
            }
        else:
            room.momentum_eligible_ids.clear()
        self._set_deadline_at(room, room.question_started_at + room.match_config.question_seconds)

    def _begin_bluff_voting(self, room: Room, *, started_at: float | None = None) -> None:
        question = room.questions[room.round_index]
        payload = question.format_payload or {}
        real_answer = str(payload.get("canonical_answer", "")).strip()
        options: list[dict[str, object]] = [
            {"id": secrets.token_urlsafe(12), "text": real_answer, "is_correct": True, "author_id": None}
        ]
        options.extend(
            {"id": secrets.token_urlsafe(12), "text": answer, "is_correct": False, "author_id": author_id}
            for author_id, answer in room.bluff_submissions.items()
        )
        secrets.SystemRandom().shuffle(options)
        room.bluff_options = options
        room.bluff_votes.clear()
        room.phase = Phase.VOTING
        started = self._monotonic() if started_at is None else started_at
        self._set_deadline_at(room, started + 15.0)

    def _reveal_bluff_results(self, room: Room, *, started_at: float | None = None) -> None:
        by_id = {str(option["id"]): option for option in room.bluff_options}
        for voter_id, option_id in room.bluff_votes.items():
            option = by_id.get(option_id)
            voter = room.players.get(voter_id)
            if option is None or voter is None:
                continue
            if option["is_correct"]:
                voter.score += 1000
                voter.correct += 1
            else:
                author = room.players.get(str(option["author_id"]))
                if author is not None:
                    author.score += 500
        room.phase = Phase.REVEAL
        started = self._monotonic() if started_at is None else started_at
        self._set_deadline_at(room, started + 5.0)

    def _escape_results(self, room: Room, outcome: str) -> dict[str, object]:
        elapsed = 0
        if room.escape_started_at is not None:
            finished_at = room.escape_completed_at if room.escape_completed_at is not None else self._monotonic()
            elapsed = max(0, int(finished_at - room.escape_started_at))
        solved = min(room.round_index, 10)
        return {
            "outcome": outcome,
            "completed_rooms": solved // 2,
            "solved_puzzles": solved,
            "remaining_hints": room.escape_hints_remaining,
            "elapsed_seconds": elapsed,
            "team_outcome": "victory" if outcome == "victory" else "failure",
        }

    def _reveal(self, room: Room, *, started_at: float | None = None) -> None:
        room.phase = Phase.REVEAL
        question = room.questions[room.round_index]
        question_start = room.question_started_at if room.question_started_at is not None else self._monotonic()
        for player in room.players.values():
            if room.match_config.elimination and player.eliminated:
                continue
            if room.match_config.royale and (player.answer is None or player.answer.choice != question.answer):
                player.lives = max(0, player.lives - 1)
                if player.lives == 0:
                    player.eliminated = True
                    player.eliminated_round = room.round_index + 1
                    player.elimination_reason = "timeout" if player.answer is None else "incorrect"
                continue
            if room.match_config.elimination and (
                player.answer is None or player.answer.choice != question.answer
            ):
                player.eliminated = True
                player.eliminated_round = room.round_index + 1
                player.elimination_reason = "timeout" if player.answer is None else "incorrect"
                continue
            if player.answer is None or player.answer.choice != question.answer:
                continue
            player.correct += 1
            elapsed = min(
                room.match_config.question_seconds,
                max(0.0, player.answer.accepted_at - question_start),
            )
            speed_bonus = int(
                room.match_config.speed_bonus_max
                * (room.match_config.question_seconds - elapsed)
                / room.match_config.question_seconds
                + 0.5
            )
            momentum_bonus = (
                room.match_config.momentum_bonus_points
                if room.match_config.momentum_enabled
                and player.id in room.momentum_eligible_ids
                and elapsed <= room.match_config.momentum_speed_seconds
                else 0
            )
            player.answer.speed_bonus = speed_bonus
            player.answer.momentum_bonus = momentum_bonus
            player.score += room.match_config.base_points + speed_bonus + momentum_bonus
            if room.match_config.power_play and player.answer:
                if player.power_double_active:
                    player.score += room.match_config.base_points + speed_bonus
                    player.answer.doubled = True
                    player.power_double_active = False
                player.power_energy = min(5, player.power_energy + 1)
            player.correct_response_time_ms += int(round(elapsed * 1000))
            if momentum_bonus:
                player.momentum_points += momentum_bonus
                room.momentum_adjustments.append(
                    {
                        "player_id": player.id,
                        "name": player.name,
                        "round": room.round_index + 1,
                        "points": momentum_bonus,
                        "reason": "Eligible player answered correctly within 5 seconds.",
                    }
                )
        if room.match_config.power_play:
            for player in room.players.values():
                answer = player.answer
                if answer is not None and answer.choice == question.answer:
                    continue
                if player.power_shield_active:
                    player.power_shield_active = False
                    if answer is None:
                        answer = Answer(choice=-1, accepted_at=question_start)
                        player.answer = answer
                    answer.shield_consumed = True
                else:
                    if answer is None:
                        answer = Answer(choice=-1, accepted_at=question_start)
                        player.answer = answer
                    player.score = max(0, player.score - 250)
                    answer.power_penalty = True
        if room.match_config.elimination:
            survivors = [player for player in room.players.values() if not player.eliminated]
            if not survivors:
                room.elimination_end_reason = "all_eliminated"
                room.terminal_elimination_round = room.round_index + 1
            elif len(survivors) == 1:
                room.elimination_end_reason = "one_survivor"
        reveal_start = self._monotonic() if started_at is None else started_at
        self._set_deadline_at(room, reveal_start + room.match_config.reveal_seconds)

    def _advance_room(self, room: Room, now: float) -> bool:
        changed = False
        while room.deadline is not None and now >= room.deadline:
            changed = True
            transition_at = room.deadline
            if room.phase == Phase.COUNTDOWN:
                self._show_question(room, started_at=transition_at)
            elif room.phase == Phase.SUBMISSION and room.match_config.bluff_master:
                self._begin_bluff_voting(room, started_at=transition_at)
            elif room.phase == Phase.VOTING and room.match_config.bluff_master:
                self._reveal_bluff_results(room, started_at=transition_at)
            elif room.phase == Phase.ESCAPE_PUZZLE and room.match_config.escape_room:
                room.phase = Phase.RESULTS
                room.escape_completed_at = transition_at
                room.deadline = None
                room.deadline_epoch_ms = None
            elif room.phase == Phase.QUESTION:
                self._reveal(room, started_at=transition_at)
            elif room.phase == Phase.REVEAL:
                if room.match_config.bluff_master:
                    room.round_index += 1
                    if room.round_index >= room.match_config.question_count:
                        room.phase = Phase.RESULTS
                        room.deadline = None
                        room.deadline_epoch_ms = None
                    else:
                        self._show_question(room, started_at=transition_at)
                elif room.match_config.elimination:
                    survivors = [player for player in room.players.values() if not player.eliminated]
                    if len(survivors) <= 1:
                        room.phase = Phase.RESULTS
                        if room.deadline is not None:
                            room.deadline = None
                            room.deadline_epoch_ms = None
                        if room.elimination_end_reason is None:
                            room.elimination_end_reason = "one_survivor" if survivors else "all_eliminated"
                            if not survivors:
                                room.terminal_elimination_round = room.round_index + 1
                    elif room.round_index + 1 >= room.match_config.question_count:
                        room.phase = Phase.RESULTS
                        room.deadline = None
                        room.deadline_epoch_ms = None
                        room.elimination_end_reason = "question_limit"
                    else:
                        room.round_index += 1
                        self._show_question(room, started_at=transition_at)
                else:
                    room.round_index += 1
                    if room.round_index >= room.match_config.question_count:
                        room.phase = Phase.RESULTS
                        room.deadline = None
                        room.deadline_epoch_ms = None
                    else:
                        self._show_question(room, started_at=transition_at)
            else:
                room.deadline = None
                room.deadline_epoch_ms = None
        return changed

    async def advance_due(self) -> list[dict[str, object]]:
        """Apply every elapsed transition; used by the scheduler and deterministic tests."""
        async with self._lock:
            now = self._monotonic()
            changed_states = []
            for room in list(self._rooms.values()):
                if self._advance_room(room, now):
                    self._touch(room)
                    changed_states.extend(self._snapshot(room, player.id) for player in room.players.values() if player.connected)
            return changed_states

    async def submit_answer(self, room_code: str, player_id: str, choice: object) -> dict[str, object]:
        async with self._lock:
            room, player = self._get_room_player(room_code, player_id)
            if room.phase != Phase.QUESTION:
                raise GameError("stale_answer", "This question is closed. Wait for the next round.")
            now = self._monotonic()
            if room.deadline is None or now >= room.deadline:
                state_changed = self._advance_room(room, now)
                if state_changed:
                    self._touch(room)
                raise GameError(
                    "stale_answer",
                    "Time is up. This question has closed.",
                    state_changed=state_changed,
                )
            if room.match_config.elimination and player.eliminated:
                raise GameError("player_eliminated", "You have been eliminated and cannot answer in later rounds.")
            if player.answer is not None:
                raise GameError("duplicate_answer", "Your answer is already locked for this round.")
            if isinstance(choice, bool) or not isinstance(choice, int) or not 0 <= choice <= 3:
                raise GameError("invalid_choice", "Choose one of the four answer options.")
            if room.match_config.power_play and player.power_fifty_round == room.round_index and choice not in player.power_fifty_choices:
                raise GameError("choice_hidden", "Choose one of the options still available to you.")
            player.answer = Answer(choice=choice, accepted_at=now)
            if room.match_config.mode == GameMode.SOLO_PRACTICE:
                player.answers_submitted += 1
            if room.match_config.elimination:
                all_answered = all(candidate.eliminated or candidate.answer is not None for candidate in room.players.values())
            else:
                all_answered = all(not candidate.connected or candidate.answer is not None for candidate in room.players.values())
            if all_answered:
                self._reveal(room)
            self._touch(room)
            return self._snapshot(room, player_id)

    async def submit_bluff(self, room_code: str, player_id: str, answer: object, request_id: object) -> dict[str, object]:
        async with self._lock:
            room, player = self._get_room_player(room_code, player_id)
            if not room.match_config.bluff_master:
                raise GameError("wrong_mode", "Fake answers are only available in Bluff Master.")
            changed = self._advance_room(room, self._monotonic())
            if room.phase != Phase.SUBMISSION:
                raise GameError("wrong_phase", "The fake-answer phase is closed.", state_changed=changed)
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", request_id):
                raise GameError("invalid_request", "Submit a valid action ID.")
            request_key = f"submit:{request_id}"
            if request_key in room.bluff_request_ids:
                raise GameError("replayed_action", "This fake answer request was already used.")
            if player_id in room.bluff_submissions:
                raise GameError("duplicate_submission", "You already submitted a fake answer this round.")
            if not isinstance(answer, str):
                raise GameError("invalid_answer", "Enter a text answer between 2 and 120 characters.")
            answer = answer.strip()
            if not 2 <= len(answer) <= 120 or any(ord(char) < 32 and char not in "\t" for char in answer):
                raise GameError("invalid_answer", "Enter a text answer between 2 and 120 characters.")
            question = room.questions[room.round_index]
            payload = question.format_payload or {}
            if open_answer_matches(payload, answer):
                raise GameError("duplicate_answer", "That matches the real answer. Try a different fake answer.")
            normalized = normalize_open_answer(answer)
            if any(normalize_open_answer(existing) == normalized for existing in room.bluff_submissions.values()):
                raise GameError("duplicate_answer", "Another player already submitted that answer. Try a different one.")
            room.bluff_request_ids.add(request_key)
            room.bluff_submissions[player_id] = answer
            if all(candidate.id in room.bluff_submissions or not candidate.connected for candidate in room.players.values()):
                self._begin_bluff_voting(room)
            self._touch(room)
            return self._snapshot(room, player_id)

    async def cast_bluff_vote(self, room_code: str, player_id: str, choice_id: object, request_id: object) -> dict[str, object]:
        async with self._lock:
            room, _ = self._get_room_player(room_code, player_id)
            if not room.match_config.bluff_master:
                raise GameError("wrong_mode", "Voting is only available in Bluff Master.")
            changed = self._advance_room(room, self._monotonic())
            if room.phase != Phase.VOTING:
                raise GameError("wrong_phase", "The voting phase is closed.", state_changed=changed)
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", request_id):
                raise GameError("invalid_request", "Submit a valid action ID.")
            request_key = f"vote:{request_id}"
            if request_key in room.bluff_request_ids:
                raise GameError("replayed_action", "This vote request was already used.")
            if player_id in room.bluff_votes:
                raise GameError("duplicate_vote", "Your vote is already locked for this round.")
            option = next((item for item in room.bluff_options if item.get("id") == choice_id), None)
            if option is None:
                raise GameError("invalid_vote", "Choose one of the displayed answers.")
            if option.get("author_id") == player_id:
                raise GameError("self_vote", "You cannot vote for your own fake answer.")
            room.bluff_request_ids.add(request_key)
            room.bluff_votes[player_id] = str(choice_id)
            if all(candidate.id in room.bluff_votes or not candidate.connected for candidate in room.players.values()):
                self._reveal_bluff_results(room)
            self._touch(room)
            return self._snapshot(room, player_id)

    async def submit_escape_answer(self, room_code: str, player_id: str, answer: object, puzzle_index: object, request_id: object) -> dict[str, object]:
        async with self._lock:
            room, _ = self._get_room_player(room_code, player_id)
            if not room.match_config.escape_room:
                raise GameError("wrong_mode", "Puzzle answers are only available in Escape Room.")
            changed = self._advance_room(room, self._monotonic())
            if room.phase != Phase.ESCAPE_PUZZLE:
                raise GameError("wrong_phase", "The Escape Room puzzle is closed.", state_changed=changed)
            if isinstance(puzzle_index, bool) or not isinstance(puzzle_index, int) or puzzle_index != room.round_index:
                raise GameError("stale_puzzle", "That puzzle has already changed. Use the current puzzle.")
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", request_id):
                raise GameError("invalid_request", "Submit a valid action ID.")
            request_key = f"answer:{request_id}"
            if request_key in room.escape_request_ids:
                raise GameError("replayed_action", "This puzzle-answer request was already used.")
            if not isinstance(answer, str) or not 1 <= len(answer.strip()) <= 200 or any(ord(char) < 32 and char not in "\t" for char in answer):
                raise GameError("invalid_answer", "Enter an answer between 1 and 200 characters.")
            now = self._monotonic()
            previous = room.escape_attempts.get(player_id)
            if previous and previous[0] == room.round_index and now - previous[1] < 0.75:
                raise GameError("rate_limited", "Wait a moment before trying another answer.")
            room.escape_request_ids.add(request_key)
            room.escape_attempts[player_id] = (room.round_index, now)
            question = room.questions[room.round_index]
            payload = question.format_payload or {}
            normalized = normalize_open_answer(answer)
            accepted = {normalize_open_answer(str(payload.get("solution", "")))}
            accepted.update(normalize_open_answer(item) for item in payload.get("accepted_aliases", []) if isinstance(item, str))
            if normalized in accepted:
                room.round_index += 1
                room.escape_active_hint = None
                if room.round_index == 10:
                    room.phase = Phase.RESULTS
                    room.escape_completed_at = now
                    room.deadline = None
                    room.deadline_epoch_ms = None
            self._touch(room)
            return self._snapshot(room, player_id)

    async def request_escape_hint(self, room_code: str, player_id: str, request_id: object) -> dict[str, object]:
        async with self._lock:
            room, _ = self._get_room_player(room_code, player_id)
            if not room.match_config.escape_room:
                raise GameError("wrong_mode", "Shared hints are only available in Escape Room.")
            changed = self._advance_room(room, self._monotonic())
            if room.phase != Phase.ESCAPE_PUZZLE:
                raise GameError("wrong_phase", "Hints are unavailable after the adventure ends.", state_changed=changed)
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", request_id):
                raise GameError("invalid_request", "Submit a valid action ID.")
            if request_id in room.escape_request_ids:
                raise GameError("replayed_action", "This hint request was already used.")
            if room.escape_hints_remaining <= 0:
                raise GameError("no_hints", "The team has used all three shared hints.")
            if room.round_index in room.escape_hinted_puzzles:
                raise GameError("hint_already_used", "The team has already used a hint for this puzzle.")
            payload = room.questions[room.round_index].format_payload or {}
            hint = payload.get("hint")
            if not isinstance(hint, str) or not hint.strip():
                raise GameError("hint_unavailable", "No hint is available for this puzzle.")
            room.escape_request_ids.add(request_id)
            room.escape_hinted_puzzles.add(room.round_index)
            room.escape_hints_remaining -= 1
            room.escape_active_hint = hint
            self._touch(room)
            return self._snapshot(room, player_id)

    async def activate_ability(self, room_code: str, player_id: str, ability: str, request_id: str) -> dict[str, object]:
        async with self._lock:
            room, player = self._get_room_player(room_code, player_id)
            if not room.match_config.power_play:
                raise GameError("wrong_mode", "Abilities are available only in Power Play.")
            if room.phase != Phase.QUESTION or room.deadline is None or self._monotonic() >= room.deadline:
                raise GameError("ability_closed", "Abilities can only be used while a question is open.")
            if player.answer is not None:
                raise GameError("already_answered", "You cannot activate an ability after locking your answer.")
            if request_id in player.power_requests:
                raise GameError("replayed_request", "That ability request has already been used.")
            if ability not in {"fifty_fifty", "double_points", "shield"}:
                raise GameError("invalid_ability", "Choose an available Power Play ability.")
            if ability in player.power_used:
                raise GameError("ability_used", "You have already used that ability this match.")
            if player.power_round_used:
                raise GameError("ability_stacked", "Only one ability can be activated per question.")
            if player.power_energy < 2:
                raise GameError("insufficient_energy", "You need 2 energy to activate an ability.")
            if ability == "fifty_fifty":
                question = room.questions[room.round_index]
                incorrect = [index for index in range(len(question.options)) if index != question.answer]
                if len(question.options) != 4 or len(incorrect) < 2:
                    raise GameError("ability_unavailable", "50:50 requires four answer options.")
                player.power_fifty_round = room.round_index
                player.power_fifty_choices = tuple(sorted([question.answer, *secrets.SystemRandom().sample(incorrect, 1)]))
            elif ability == "double_points":
                player.power_double_active = True
            else:
                player.power_shield_active = True
            player.power_energy -= 2
            player.power_used.add(ability)
            player.power_requests.add(request_id)
            player.power_round_used = True
            self._touch(room)
            return self._snapshot(room, player_id)

    async def set_lobby(self, room_code: str, player_id: str, *, rematch: bool = False) -> dict[str, object]:
        async with self._lock:
            room, _ = self._get_room_player(room_code, player_id)
            if room.host_id != player_id:
                raise GameError("host_only", "Only the host can change the arena after a game.")
            if room.phase != Phase.RESULTS:
                raise GameError("wrong_phase", "The quiz must finish before returning to the lobby.")
            if (
                room.match_config.mode == GameMode.TEAM_BATTLE
                or room.match_config.elimination
                or room.match_config.bluff_master
                or room.match_config.escape_room
            ):
                self._prune_expired_players(room, self._monotonic())
            room.phase = Phase.WAITING
            room.questions = ()
            room.round_index = 0
            room.deadline = None
            room.deadline_epoch_ms = None
            room.question_started_at = None
            room.bluff_submissions.clear()
            room.bluff_options.clear()
            room.bluff_votes.clear()
            room.bluff_request_ids.clear()
            room.escape_hints_remaining = 3
            room.escape_active_hint = None
            room.escape_request_ids.clear()
            room.escape_hinted_puzzles.clear()
            room.escape_attempts.clear()
            room.escape_started_at = None
            room.escape_completed_at = None
            for player in room.players.values():
                player.score = 0
                player.correct = 0
                player.answers_submitted = 0
                player.momentum_points = 0
                player.correct_response_time_ms = 0
                player.eliminated = False
                player.eliminated_round = None
                player.elimination_reason = None
                player.answer = None
                player.lives = room.match_config.starting_lives
                player.power_energy = 3 if room.match_config.power_play else 0
                player.power_used.clear()
                player.power_requests.clear()
                player.power_double_active = False
                player.power_shield_active = False
                player.power_fifty_round = None
                player.power_fifty_choices = ()
                player.power_round_used = False
            room.momentum_eligible_ids.clear()
            room.momentum_adjustments.clear()
            room.elimination_end_reason = None
            room.terminal_elimination_round = None
            self._touch(room)
            return self._snapshot(room, player_id)

    async def disconnect(self, room_code: str, player_id: str) -> list[dict[str, object]]:
        async with self._lock:
            room, player = self._get_room_player(room_code, player_id)
            player.connected = False
            player.disconnected_at = self._monotonic()
            if room.host_id == player_id:
                successor = next((candidate for candidate in room.players.values() if candidate.connected), None)
                if successor:
                    room.host_id = successor.id
            self._touch(room)
            return [self._snapshot(room, candidate.id) for candidate in room.players.values() if candidate.connected]

    async def cleanup(self) -> list[str]:
        async with self._lock:
            now = self._monotonic()
            removed: list[str] = []
            for code, room in list(self._rooms.items()):
                for player_id, player in list(room.players.items()):
                    if not player.connected and player.disconnected_at is not None and now - player.disconnected_at > RECONNECT_SECONDS:
                        if (
                            room.phase != Phase.WAITING
                            and (
                                room.match_config.mode == GameMode.TEAM_BATTLE
                                or room.match_config.elimination
                                or room.match_config.bluff_master
                                or room.match_config.escape_room
                            )
                        ):
                            player.reconnect_expired = True
                            self._sessions.pop(player.token_hash, None)
                        else:
                            self._remove_player(room, player_id)
                if not room.players or (
                    not any(player.connected for player in room.players.values())
                    and now - room.last_activity > RECONNECT_SECONDS
                ) or now - room.last_activity > ROOM_IDLE_SECONDS:
                    for player in room.players.values():
                        self._sessions.pop(player.token_hash, None)
                    del self._rooms[code]
                    removed.append(code)
            return removed

    def _remove_player(self, room: Room, player_id: str) -> None:
        player = room.players.pop(player_id, None)
        if player is None:
            return
        self._sessions.pop(player.token_hash, None)
        if room.host_id == player_id:
            successor = next((candidate.id for candidate in room.players.values() if candidate.connected), None)
            room.host_id = successor or next(iter(room.players), "")

    def _prune_expired_players(self, room: Room, now: float) -> None:
        for player_id, player in list(room.players.items()):
            if not player.connected and player.disconnected_at is not None and now - player.disconnected_at > RECONNECT_SECONDS:
                self._remove_player(room, player_id)

    async def snapshot(self, room_code: str, player_id: str) -> dict[str, object]:
        async with self._lock:
            room, _ = self._get_room_player(room_code, player_id)
            return self._snapshot(room, player_id)

    async def room_codes(self) -> tuple[str, ...]:
        async with self._lock:
            return tuple(self._rooms)

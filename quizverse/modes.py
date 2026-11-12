"""Server-owned match modes and immutable rule configuration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class GameMode(StrEnum):
    CLASSIC = "classic"
    SPEED_CHALLENGE = "speed_challenge"
    TEAM_BATTLE = "team_battle"
    ELIMINATION = "elimination"
    SOLO_PRACTICE = "solo_practice"
    QUIZ_ROYALE = "quiz_royale"
    POWER_PLAY = "power_play"
    BLUFF_MASTER = "bluff_master"
    ESCAPE_ROOM = "escape_room"


@dataclass(frozen=True, slots=True)
class ModeConfig:
    """An immutable ruleset selected by a server-validated mode identifier."""

    mode: GameMode
    min_players: int
    max_players: int
    question_count: int
    countdown_seconds: float
    question_seconds: float
    reveal_seconds: float
    base_points: int
    speed_bonus_max: int
    momentum_enabled: bool
    momentum_gap_points: int
    momentum_speed_seconds: float
    momentum_bonus_points: int
    even_player_count: bool = False
    elimination: bool = False
    royale: bool = False
    power_play: bool = False
    starting_lives: int = 0
    bluff_master: bool = False
    escape_room: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.min_players <= self.max_players <= (20 if self.royale else 8):
            raise ValueError("Mode player limits exceed this mode's supported roster size.")
        if self.question_count < 1:
            raise ValueError("A mode needs at least one question.")
        if min(self.countdown_seconds, self.question_seconds, self.reveal_seconds) <= 0:
            raise ValueError("Mode phase durations must be positive.")
        if min(self.base_points, self.speed_bonus_max, self.momentum_gap_points, self.momentum_bonus_points) < 0:
            raise ValueError("Mode scoring values cannot be negative.")
        if self.momentum_speed_seconds < 0:
            raise ValueError("Momentum timing cannot be negative.")


CLASSIC_CONFIG = ModeConfig(
    mode=GameMode.CLASSIC,
    min_players=2,
    max_players=8,
    question_count=10,
    countdown_seconds=3.0,
    question_seconds=15.0,
    reveal_seconds=3.0,
    base_points=1000,
    speed_bonus_max=500,
    momentum_enabled=True,
    momentum_gap_points=1500,
    momentum_speed_seconds=5.0,
    momentum_bonus_points=250,
)

SPEED_CHALLENGE_CONFIG = ModeConfig(
    mode=GameMode.SPEED_CHALLENGE,
    min_players=2,
    max_players=8,
    question_count=10,
    countdown_seconds=3.0,
    question_seconds=10.0,
    reveal_seconds=3.0,
    base_points=1000,
    speed_bonus_max=500,
    momentum_enabled=False,
    momentum_gap_points=0,
    momentum_speed_seconds=0.0,
    momentum_bonus_points=0,
)

TEAM_BATTLE_CONFIG = ModeConfig(
    mode=GameMode.TEAM_BATTLE,
    min_players=4,
    max_players=8,
    question_count=10,
    countdown_seconds=3.0,
    question_seconds=15.0,
    reveal_seconds=3.0,
    base_points=1000,
    speed_bonus_max=500,
    momentum_enabled=False,
    momentum_gap_points=0,
    momentum_speed_seconds=0.0,
    momentum_bonus_points=0,
    even_player_count=True,
)

ELIMINATION_CONFIG = ModeConfig(
    mode=GameMode.ELIMINATION,
    min_players=2,
    max_players=8,
    question_count=10,
    countdown_seconds=3.0,
    question_seconds=15.0,
    reveal_seconds=3.0,
    base_points=1000,
    speed_bonus_max=500,
    momentum_enabled=False,
    momentum_gap_points=0,
    momentum_speed_seconds=0.0,
    momentum_bonus_points=0,
    elimination=True,
)

SOLO_PRACTICE_CONFIG = ModeConfig(
    mode=GameMode.SOLO_PRACTICE,
    min_players=1,
    max_players=1,
    question_count=10,
    countdown_seconds=3.0,
    question_seconds=15.0,
    reveal_seconds=3.0,
    base_points=1000,
    speed_bonus_max=500,
    momentum_enabled=False,
    momentum_gap_points=0,
    momentum_speed_seconds=0.0,
    momentum_bonus_points=0,
)

QUIZ_ROYALE_CONFIG = ModeConfig(
    mode=GameMode.QUIZ_ROYALE, min_players=4, max_players=20, question_count=15,
    countdown_seconds=3.0, question_seconds=12.0, reveal_seconds=3.0,
    base_points=1000, speed_bonus_max=500, momentum_enabled=False,
    momentum_gap_points=0, momentum_speed_seconds=0.0, momentum_bonus_points=0,
    elimination=True, royale=True, starting_lives=3,
)

POWER_PLAY_CONFIG = ModeConfig(
    mode=GameMode.POWER_PLAY, min_players=2, max_players=8, question_count=10,
    countdown_seconds=3.0, question_seconds=15.0, reveal_seconds=3.0,
    base_points=1000, speed_bonus_max=500, momentum_enabled=False,
    momentum_gap_points=0, momentum_speed_seconds=0.0, momentum_bonus_points=0,
    power_play=True,
)

BLUFF_MASTER_CONFIG = ModeConfig(
    mode=GameMode.BLUFF_MASTER, min_players=3, max_players=8, question_count=10,
    countdown_seconds=3.0, question_seconds=30.0, reveal_seconds=5.0,
    base_points=1000, speed_bonus_max=0, momentum_enabled=False,
    momentum_gap_points=0, momentum_speed_seconds=0.0, momentum_bonus_points=0,
    bluff_master=True,
)

ESCAPE_ROOM_CONFIG = ModeConfig(
    mode=GameMode.ESCAPE_ROOM, min_players=2, max_players=8, question_count=10,
    countdown_seconds=3.0, question_seconds=900.0, reveal_seconds=5.0,
    base_points=0, speed_bonus_max=0, momentum_enabled=False,
    momentum_gap_points=0, momentum_speed_seconds=0.0, momentum_bonus_points=0,
    escape_room=True,
)

_SUPPORTED_MODES = MappingProxyType(
    {
        GameMode.CLASSIC: CLASSIC_CONFIG,
        GameMode.SPEED_CHALLENGE: SPEED_CHALLENGE_CONFIG,
        GameMode.TEAM_BATTLE: TEAM_BATTLE_CONFIG,
        GameMode.ELIMINATION: ELIMINATION_CONFIG,
        GameMode.SOLO_PRACTICE: SOLO_PRACTICE_CONFIG,
        GameMode.QUIZ_ROYALE: QUIZ_ROYALE_CONFIG,
        GameMode.POWER_PLAY: POWER_PLAY_CONFIG,
        GameMode.BLUFF_MASTER: BLUFF_MASTER_CONFIG,
        GameMode.ESCAPE_ROOM: ESCAPE_ROOM_CONFIG,
    }
)


def resolve_mode(mode: object = None) -> ModeConfig:
    """Return a server-owned ruleset; clients can select an ID, never its rules."""

    selected = GameMode.CLASSIC if mode is None else mode
    if isinstance(selected, str):
        try:
            selected = GameMode(selected)
        except ValueError as error:
            raise ValueError("Unsupported game mode.") from error
    if not isinstance(selected, GameMode):
        raise ValueError("Unsupported game mode.")
    config = _SUPPORTED_MODES.get(selected)
    if config is None:
        raise ValueError("Unsupported game mode.")
    return config

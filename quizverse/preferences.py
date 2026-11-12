"""Validated, account-free player preferences and host filter values."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Mapping


class LearningPreset(StrEnum):
    LITTLE_EXPLORERS = "little_explorers"
    YOUNG_LEARNERS = "young_learners"
    JUNIOR_CHAMPIONS = "junior_champions"
    TEEN_CHALLENGE = "teen_challenge"
    COLLEGE_ARENA = "college_arena"
    EXPERT_ARENA = "expert_arena"


class TopicPreference(StrEnum):
    INCREASE = "increase"
    NORMAL = "normal"
    DECREASE = "decrease"
    EXCLUDE = "exclude"


TOPIC_WEIGHTS: Mapping[TopicPreference, float] = {
    TopicPreference.INCREASE: 3.0,
    TopicPreference.NORMAL: 1.0,
    TopicPreference.DECREASE: 0.3,
    TopicPreference.EXCLUDE: 0.0,
}


@dataclass(frozen=True, slots=True)
class PlayerPreferences:
    learning_preset: LearningPreset = LearningPreset.TEEN_CHALLENGE
    difficulty: str = "medium"
    language: str = "en"
    topics: Mapping[str, TopicPreference] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: object) -> "PlayerPreferences":
        if not isinstance(raw, dict):
            return cls()
        try:
            preset = LearningPreset(raw.get("learning_preset", "teen_challenge"))
            difficulty = raw.get("difficulty", "medium")
            if difficulty not in {"easy", "medium", "hard", "mixed"}:
                raise ValueError
            language = raw.get("language", "en")
            if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
                raise ValueError
            raw_topics = raw.get("topics", {})
            if not isinstance(raw_topics, dict) or len(raw_topics) > 100:
                raise ValueError
            topics = {
                key: TopicPreference(value)
                for key, value in raw_topics.items()
                if isinstance(key, str) and 1 <= len(key) <= 128
            }
            if len(topics) != len(raw_topics):
                raise ValueError
            return cls(preset, difficulty, language.lower(), topics)
        except (ValueError, TypeError):
            return cls()


def resolve_topic_preference(category_id: str | None, parent_by_id: Mapping[str, str | None], preferences: Mapping[str, TopicPreference]) -> TopicPreference:
    """Use nearest explicit category preference, inheriting towards the root."""
    current = category_id
    visited: set[str] = set()
    while current is not None:
        if current in visited:
            return TopicPreference.NORMAL
        visited.add(current)
        if current in preferences:
            return preferences[current]
        current = parent_by_id.get(current)
    return TopicPreference.NORMAL


def mixed_difficulty_counts(question_count: int) -> dict[str, int]:
    """Allocate 25/50/25 using largest remainders, stable on ties."""
    if isinstance(question_count, bool) or not isinstance(question_count, int) or question_count < 1:
        raise ValueError("Question count must be a positive integer.")
    proportions = (("easy", 0.25), ("medium", 0.50), ("hard", 0.25))
    counts = {name: int(question_count * share) for name, share in proportions}
    remaining = question_count - sum(counts.values())
    order = sorted(proportions, key=lambda pair: (-(question_count * pair[1] - counts[pair[0]]), pair[0]))
    for name, _ in order[:remaining]:
        counts[name] += 1
    return counts

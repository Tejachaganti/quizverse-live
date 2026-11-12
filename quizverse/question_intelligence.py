"""Shared, server-side question filtering and weighted selection primitives."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol, TypeVar

from quizverse.preferences import LearningPreset, TOPIC_WEIGHTS, TopicPreference, mixed_difficulty_counts, resolve_topic_preference


class SelectableQuestion(Protocol):
    id: str
    category_id: str | None
    difficulty: str | None
    language: str
    learning_levels: list[str]
    review_status: str
    question_format: str
    exam_collection_ids: set[str]


T = TypeVar("T", bound=SelectableQuestion)


@dataclass(frozen=True, slots=True)
class SelectionFilters:
    count: int
    difficulty: str = "medium"
    language: str = "en"
    learning_preset: LearningPreset = LearningPreset.TEEN_CHALLENGE
    categories: frozenset[str] = frozenset()
    excluded_categories: frozenset[str] = frozenset()
    exam_collection_id: str | None = None
    question_format: str = "multiple_choice"
    enforce_reviewed: bool = False
    parent_by_id: Mapping[str, str | None] | None = None
    topic_preferences: Mapping[str, TopicPreference] | None = None


class InsufficientInventory(ValueError):
    def __init__(self, requested: int, eligible: int, missing: Mapping[str, int] | None = None):
        self.requested, self.eligible, self.missing = requested, eligible, dict(missing or {})
        detail = f"Only {eligible} eligible questions are available; {requested} requested."
        if self.missing:
            detail += " Missing difficulty inventory: " + ", ".join(f"{key} {value}" for key, value in self.missing.items()) + "."
        super().__init__(detail)


def _eligible(question: T, filters: SelectionFilters) -> bool:
    if question.question_format != filters.question_format or question.language.lower() != filters.language.lower():
        return False
    if filters.categories:
        current = question.category_id
        visited: set[str] = set()
        in_scope = False
        while current is not None and current not in visited:
            if current in filters.categories:
                in_scope = True
                break
            visited.add(current)
            current = (filters.parent_by_id or {}).get(current)
        if not in_scope:
            return False
    if question.category_id in filters.excluded_categories:
        return False
    if filters.exam_collection_id and filters.exam_collection_id not in question.exam_collection_ids:
        return False
    if filters.enforce_reviewed and question.review_status != "reviewed":
        return False
    if filters.learning_preset == LearningPreset.LITTLE_EXPLORERS:
        if question.review_status != "reviewed" or LearningPreset.LITTLE_EXPLORERS.value not in question.learning_levels:
            return False
    elif question.learning_levels and filters.learning_preset.value not in question.learning_levels:
        return False
    pref = resolve_topic_preference(question.category_id, filters.parent_by_id or {}, filters.topic_preferences or {})
    return TOPIC_WEIGHTS[pref] > 0


def select_questions(
    questions: Iterable[T], filters: SelectionFilters, *, rng: object | None = None,
) -> tuple[T, ...]:
    """Filter hard constraints, then weighted sample without replacement."""
    if isinstance(filters.count, bool) or not isinstance(filters.count, int) or not 1 <= filters.count <= 100:
        raise ValueError("Question count must be between 1 and 100.")
    if filters.difficulty not in {"easy", "medium", "hard", "mixed"}:
        raise ValueError("Unsupported difficulty.")
    eligible = [q for q in questions if _eligible(q, filters) and (filters.difficulty == "mixed" or q.difficulty == filters.difficulty)]
    if len({q.id for q in eligible}) != len(eligible):
        raise ValueError("Question inventory contains duplicate IDs.")
    target = mixed_difficulty_counts(filters.count) if filters.difficulty == "mixed" else {}
    if len(eligible) < filters.count:
        missing = {key: max(0, amount - sum(q.difficulty == key for q in eligible)) for key, amount in target.items()}
        raise InsufficientInventory(filters.count, len(eligible), missing)
    randomizer = rng or secrets.SystemRandom()
    selected: list[T] = []
    remaining = list(eligible)
    for difficulty, amount in target.items():
        bucket = [q for q in remaining if q.difficulty == difficulty]
        take = min(amount, len(bucket))
        selected.extend(_weighted_draw(bucket, take, filters, randomizer))
        chosen_ids = {q.id for q in selected}
        remaining = [q for q in remaining if q.id not in chosen_ids]
    selected.extend(_weighted_draw(remaining, filters.count - len(selected), filters, randomizer))
    return tuple(selected)


def _weighted_draw(pool: list[T], count: int, filters: SelectionFilters, rng: object) -> list[T]:
    result: list[T] = []
    candidates = list(pool)
    for _ in range(count):
        weights = [TOPIC_WEIGHTS[resolve_topic_preference(q.category_id, filters.parent_by_id or {}, filters.topic_preferences or {})] for q in candidates]
        total = sum(weights)
        if total <= 0:
            break
        needle = rng.random() * total  # type: ignore[attr-defined]
        index = 0
        for index, weight in enumerate(weights):
            needle -= weight
            if needle < 0:
                break
        result.append(candidates.pop(index))
    return result

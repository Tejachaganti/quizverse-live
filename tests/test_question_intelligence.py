from dataclasses import dataclass

import pytest

from quizverse.preferences import LearningPreset, PlayerPreferences, TopicPreference, mixed_difficulty_counts, resolve_topic_preference
from quizverse.question_intelligence import InsufficientInventory, SelectionFilters, select_questions


@dataclass(frozen=True)
class Q:
    id: str
    category_id: str
    difficulty: str
    language: str = "en"
    learning_levels: list[str] = None
    review_status: str = "reviewed"
    question_format: str = "multiple_choice"
    exam_collection_ids: set[str] = None

    def __post_init__(self):
        object.__setattr__(self, "learning_levels", self.learning_levels or ["teen_challenge", "little_explorers"])
        object.__setattr__(self, "exam_collection_ids", self.exam_collection_ids or set())


def bank():
    return [Q(f"{d}-{i}", "science", d) for d in ("easy", "medium", "hard") for i in range(8)] + [Q(f"m{i}", "arts", "medium") for i in range(8)]


def test_preference_corruption_defaults_and_validation():
    assert PlayerPreferences.parse(None).learning_preset.value == "teen_challenge"
    assert PlayerPreferences.parse({"difficulty": "chaos"}).difficulty == "medium"
    assert PlayerPreferences.parse({"topics": {"bad": "wat"}}).topics == {}


def test_parent_inheritance_and_specific_override():
    parents = {"child": "parent", "parent": None}
    assert resolve_topic_preference("child", parents, {"parent": TopicPreference.EXCLUDE}) == TopicPreference.EXCLUDE
    assert resolve_topic_preference("child", parents, {"parent": TopicPreference.EXCLUDE, "child": TopicPreference.INCREASE}) == TopicPreference.INCREASE


def test_mixed_distribution_is_deterministic_and_exact():
    assert mixed_difficulty_counts(4) == {"easy": 1, "medium": 2, "hard": 1}
    assert mixed_difficulty_counts(7) == {"easy": 2, "medium": 3, "hard": 2}
    assert sum(mixed_difficulty_counts(13).values()) == 13


def test_hard_filters_no_duplicates_and_insufficient_inventory():
    filters = SelectionFilters(5, language="en", excluded_categories=frozenset({"science"}))
    selected = select_questions(bank(), filters)
    assert len(selected) == len({q.id for q in selected}) == 5
    assert all(q.category_id == "arts" for q in selected)
    with pytest.raises(InsufficientInventory, match="Only 0 eligible"):
        select_questions(bank(), SelectionFilters(1, language="fr"))


def test_youngest_preset_requires_review_and_suitability():
    inventory = bank() + [Q("unknown", "arts", "medium", learning_levels=[], review_status="unreviewed")]
    selected = select_questions(inventory, SelectionFilters(2, learning_preset=LearningPreset.LITTLE_EXPLORERS))
    assert all(q.review_status == "reviewed" and "little_explorers" in q.learning_levels for q in selected)

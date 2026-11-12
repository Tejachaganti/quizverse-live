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
    learning_levels: list[str] | None = None
    review_status: str = "reviewed"
    question_format: str = "multiple_choice"
    exam_collection_ids: set[str] = None

    def __post_init__(self):
        if self.learning_levels is None:
            object.__setattr__(self, "learning_levels", ["teen_challenge", "little_explorers"])
        object.__setattr__(self, "exam_collection_ids", self.exam_collection_ids or set())


def bank():
    return [Q(f"{d}-{i}", "science", d) for d in ("easy", "medium", "hard") for i in range(8)] + [Q(f"m{i}", "arts", "medium") for i in range(8)]


def test_preference_corruption_defaults_and_validation():
    assert PlayerPreferences.parse(None).learning_preset.value == "teen_challenge"
    assert PlayerPreferences.parse({"difficulty": "chaos"}).difficulty == "medium"
    assert PlayerPreferences.parse({"topics": {"bad": "wat"}}).topics == {}
    assert PlayerPreferences.parse({"language": "hi-IN"}).language == "hi-in"
    assert PlayerPreferences.parse({"language": "not a tag"}).language == "en"


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
    inventory = bank() + [
        Q("unknown", "arts", "medium", learning_levels=[], review_status="unreviewed"),
        Q("unlabeled-reviewed", "arts", "medium", learning_levels=[], review_status="reviewed"),
    ]
    selected = select_questions(inventory, SelectionFilters(2, learning_preset=LearningPreset.LITTLE_EXPLORERS))
    assert all(q.review_status == "reviewed" and "little_explorers" in q.learning_levels for q in selected)


def test_weighted_sampling_is_injectable_and_exclusion_inherits():
    class FixedRandom:
        def __init__(self, value): self.value = value
        def random(self): return self.value

    inventory = [Q("parent-child", "child", "medium"), Q("other", "other", "medium")]
    filters = SelectionFilters(
        1,
        parent_by_id={"child": "parent", "parent": None},
        topic_preferences={"parent": TopicPreference.EXCLUDE},
    )
    assert select_questions(inventory, filters, rng=FixedRandom(0))[0].id == "other"
    override = SelectionFilters(
        1,
        parent_by_id={"child": "parent", "parent": None},
        topic_preferences={"parent": TopicPreference.EXCLUDE, "child": TopicPreference.INCREASE},
    )
    assert select_questions(inventory, override, rng=FixedRandom(0))[0].id == "parent-child"


def test_mixed_distribution_and_format_and_exam_filters():
    inventory = [
        Q(f"{level}-{index}", "science", level, exam_collection_ids={"exam-1"}, question_format="multiple_choice")
        for level in ("easy", "medium", "hard") for index in range(4)
    ]
    selected = select_questions(inventory, SelectionFilters(4, difficulty="mixed", exam_collection_id="exam-1"))
    assert {level: sum(q.difficulty == level for q in selected) for level in ("easy", "medium", "hard")} == {"easy": 1, "medium": 2, "hard": 1}
    with pytest.raises(InsufficientInventory):
        select_questions(inventory, SelectionFilters(1, exam_collection_id="exam-2"))

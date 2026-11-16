"""Unit tests for LearningPathGenerator."""

from __future__ import annotations

import pytest

from quizverse.intelligence.learning_paths import LearningPathGenerator


def test_generate_learning_path_structure():
    generator = LearningPathGenerator()
    weak_topics = [{"topic_id": "cat:history", "topic_name": "World History", "accuracy_pct": 40.0}]
    strong_topics = [{"topic_id": "cat:math", "topic_name": "Mathematics", "accuracy_pct": 90.0}]
    preferences = {"topics": {}}
    quizzes = [
        {"id": "q_hist_1", "title": "World History 101", "category": "World History"},
        {"id": "q_hist_2", "title": "Advanced World History", "category": "World History"},
    ]

    path = generator.generate_path(
        weak_topics=weak_topics,
        strong_topics=strong_topics,
        preferences=preferences,
        available_quizzes=quizzes,
        recommended_difficulty="medium",
    )

    assert "World History" in path.title
    assert path.target_category == "World History"
    assert path.recommended_difficulty == "medium"
    assert len(path.steps) == 4

    assert path.steps[0].step_number == 1
    assert path.steps[0].activity_type == "topic_deep_dive"
    assert path.steps[1].step_number == 2
    assert path.steps[1].activity_type == "quiz_practice"
    assert path.steps[2].step_number == 3
    assert path.steps[2].activity_type == "speed_drill"
    assert path.steps[3].step_number == 4
    assert path.steps[3].activity_type == "exam_prep"


def test_generate_path_from_preferences_when_no_weaknesses():
    generator = LearningPathGenerator()
    preferences = {"topics": {"category:physics": "increase"}}

    path = generator.generate_path(
        weak_topics=[],
        strong_topics=[],
        preferences=preferences,
        recommended_difficulty="hard",
    )

    assert "Physics" in path.target_category
    assert path.recommended_difficulty == "hard"
    assert len(path.steps) == 4

"""Unit tests for AdaptiveDifficultyEngine."""

from __future__ import annotations

import pytest

from quizverse.intelligence.adaptive_difficulty import AdaptiveDifficultyEngine


def test_insufficient_evidence_maintains_difficulty():
    engine = AdaptiveDifficultyEngine()
    attempts = [
        {"correct_count": 8, "answer_count": 10},
        {"correct_count": 9, "answer_count": 10},
    ]  # only 2 attempts, minimum is 3
    result = engine.evaluate(current_difficulty="medium", attempts=attempts)
    assert result.recommended_difficulty == "medium"
    assert result.adjustment_direction == "maintain"
    assert "insufficient" in result.reasoning.lower()
    assert result.confidence < 0.6


def test_high_accuracy_promotes_difficulty():
    engine = AdaptiveDifficultyEngine()
    attempts = [
        {"correct_count": 9, "answer_count": 10},
        {"correct_count": 8, "answer_count": 10},
        {"correct_count": 9, "answer_count": 10},
        {"correct_count": 9, "answer_count": 10},
    ]  # 35/40 = 87.5% accuracy
    result = engine.evaluate(current_difficulty="medium", attempts=attempts)
    assert result.recommended_difficulty == "hard"
    assert result.adjustment_direction == "increase"
    assert result.confidence >= 0.75


def test_promotion_from_easy_to_medium():
    engine = AdaptiveDifficultyEngine()
    attempts = [
        {"correct_count": 10, "answer_count": 10},
        {"correct_count": 10, "answer_count": 10},
        {"correct_count": 10, "answer_count": 10},
    ]  # 100% accuracy
    result = engine.evaluate(current_difficulty="easy", attempts=attempts)
    assert result.recommended_difficulty == "medium"
    assert result.adjustment_direction == "increase"


def test_low_accuracy_demotes_difficulty():
    engine = AdaptiveDifficultyEngine()
    attempts = [
        {"correct_count": 2, "answer_count": 10},
        {"correct_count": 3, "answer_count": 10},
        {"correct_count": 3, "answer_count": 10},
    ]  # 8/30 = 26.7% accuracy
    result = engine.evaluate(current_difficulty="hard", attempts=attempts)
    assert result.recommended_difficulty == "medium"
    assert result.adjustment_direction == "decrease"


def test_demotion_from_medium_to_easy():
    engine = AdaptiveDifficultyEngine()
    attempts = [
        {"correct_count": 1, "answer_count": 10},
        {"correct_count": 2, "answer_count": 10},
        {"correct_count": 1, "answer_count": 10},
    ]  # 4/30 = 13.3% accuracy
    result = engine.evaluate(current_difficulty="medium", attempts=attempts)
    assert result.recommended_difficulty == "easy"
    assert result.adjustment_direction == "decrease"


def test_moderate_accuracy_maintains_difficulty():
    engine = AdaptiveDifficultyEngine()
    attempts = [
        {"correct_count": 6, "answer_count": 10},
        {"correct_count": 7, "answer_count": 10},
        {"correct_count": 6, "answer_count": 10},
    ]  # 19/30 = 63.3% accuracy
    result = engine.evaluate(current_difficulty="medium", attempts=attempts)
    assert result.recommended_difficulty == "medium"
    assert result.adjustment_direction == "maintain"

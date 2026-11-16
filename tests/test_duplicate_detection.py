"""Unit tests for DuplicateDetector."""

from __future__ import annotations

import pytest

from quizverse.intelligence.duplicate_detection import DuplicateDetector


def test_exact_duplicate():
    detector = DuplicateDetector()
    candidates = [
        {"id": "q1", "prompt": "What is the capital of France?"},
        {"id": "q2", "prompt": "What is the speed of light?"},
    ]
    res = detector.check_duplicate("What is the capital of France?", candidates)
    assert res.duplicate_status == "exact_duplicate"
    assert res.similarity_score == 1.0
    assert res.review_required is True
    assert res.matched_question_id == "q1"


def test_normalized_duplicate():
    detector = DuplicateDetector()
    candidates = [
        {"id": "q1", "prompt": "What is the capital of France?"},
    ]
    res = detector.check_duplicate("WHAT IS THE CAPITAL OF FRANCE?!", candidates)
    assert res.duplicate_status == "normalized_duplicate"
    assert res.similarity_score >= 0.95
    assert res.review_required is True
    assert res.matched_question_id == "q1"


def test_near_duplicate():
    detector = DuplicateDetector()
    candidates = [
        {"id": "q1", "prompt": "Which planet is closest to the Sun?"},
    ]
    res = detector.check_duplicate("Which planet is the closest planet to our Sun?", candidates)
    assert res.similarity_score >= 0.70
    assert res.matched_question_id == "q1"


def test_unique_question():
    detector = DuplicateDetector()
    candidates = [
        {"id": "q1", "prompt": "What is the boiling point of water in Celsius?"},
    ]
    res = detector.check_duplicate("Who wrote Romeo and Juliet?", candidates)
    assert res.duplicate_status == "unique"
    assert res.review_required is False
    assert res.similarity_score < 0.35

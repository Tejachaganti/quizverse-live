"""Unit tests for WeaknessDetector."""

from __future__ import annotations

import pytest

from quizverse.intelligence.weakness_detection import WeaknessDetector


def test_classify_strong_and_weak_topics():
    detector = WeaknessDetector()
    topic_attempts = {
        "cat:physics": [
            {"correct_count": 9, "answer_count": 10, "response_time_ms": 3000},
            {"correct_count": 10, "answer_count": 10, "response_time_ms": 3200},
            {"correct_count": 9, "answer_count": 10, "response_time_ms": 2800},
        ],  # 28/30 = 93.3% -> Strong
        "cat:chemistry": [
            {"correct_count": 3, "answer_count": 10, "response_time_ms": 7000},
            {"correct_count": 4, "answer_count": 10, "response_time_ms": 6500},
            {"correct_count": 3, "answer_count": 10, "response_time_ms": 7200},
        ],  # 10/30 = 33.3% -> Weak
        "cat:history": [
            {"correct_count": 6, "answer_count": 10, "response_time_ms": 4500},
            {"correct_count": 7, "answer_count": 10, "response_time_ms": 4000},
            {"correct_count": 6, "answer_count": 10, "response_time_ms": 4200},
        ],  # 19/30 = 63.3% -> Needs Practice
    }
    category_names = {
        "cat:physics": "Physics",
        "cat:chemistry": "Chemistry",
        "cat:history": "History",
    }
    summary = detector.analyze_topics(topic_attempts, category_names)
    assert summary.total_topics_analyzed == 3

    assert len(summary.strong_topics) == 1
    assert summary.strong_topics[0]["topic_name"] == "Physics"
    assert summary.strong_topics[0]["accuracy_pct"] > 90.0

    assert len(summary.weak_topics) == 1
    assert summary.weak_topics[0]["topic_name"] == "Chemistry"
    assert summary.weak_topics[0]["accuracy_pct"] < 50.0

    assert len(summary.needs_practice_topics) == 1
    assert summary.needs_practice_topics[0]["topic_name"] == "History"


def test_trend_detection():
    detector = WeaknessDetector()
    # 4 attempts: recent half has much higher accuracy than older half -> improving
    topic_attempts = {
        "cat:math": [
            {"correct_count": 10, "answer_count": 10},  # recent
            {"correct_count": 9, "answer_count": 10},   # recent
            {"correct_count": 3, "answer_count": 10},   # older
            {"correct_count": 4, "answer_count": 10},   # older
        ],
    }
    summary = detector.analyze_topics(topic_attempts)
    topics = summary.strong_topics + summary.weak_topics + summary.needs_practice_topics
    math_topic = next((t for t in topics if "math" in t["topic_id"].lower()), None)
    assert math_topic is not None
    assert math_topic["trend"] == "improving"


def test_insufficient_data():
    detector = WeaknessDetector()
    topic_attempts = {
        "cat:astronomy": [
            {"correct_count": 1, "answer_count": 10},
        ],  # Only 1 attempt, less than MIN_SAMPLE_THRESHOLD (3)
    }
    summary = detector.analyze_topics(topic_attempts)
    assert len(summary.insufficient_data_topics) == 1
    assert summary.insufficient_data_topics[0]["status"] == "insufficient_data"

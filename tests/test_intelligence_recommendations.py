"""Unit tests for QuizRecommender."""

from __future__ import annotations

import pytest

from quizverse.intelligence.recommendations import QuizRecommender


def test_recommend_weak_topic():
    recommender = QuizRecommender()
    weak_topics = [{"topic_id": "cat:geo", "topic_name": "Geography", "accuracy_pct": 35.0}]
    strong_topics = [{"topic_id": "cat:math", "topic_name": "Mathematics", "accuracy_pct": 95.0}]
    available_quizzes = [
        {"id": "q1", "title": "World Geography Quest", "category": "Geography", "difficulty": "medium"},
        {"id": "q2", "title": "Advanced Calculus", "category": "Mathematics", "difficulty": "hard"},
    ]

    recs = recommender.recommend(
        weak_topics=weak_topics,
        strong_topics=strong_topics,
        available_quizzes=available_quizzes,
        excluded_categories=set(),
        recommended_difficulty="medium",
        limit=5,
    )

    assert len(recs) >= 1
    # First recommendation should target the weak topic Geography
    assert recs[0].category == "Geography"
    assert recs[0].reason_type == "weak_topic"
    assert "geography" in recs[0].reason_text.lower()
    assert recs[0].score >= 0.90


def test_respect_excluded_categories():
    recommender = QuizRecommender()
    weak_topics = [{"topic_id": "cat:geo", "topic_name": "Geography", "accuracy_pct": 35.0}]
    strong_topics = []
    available_quizzes = [
        {"id": "q1", "title": "World Geography Quest", "category": "Geography", "difficulty": "medium"},
        {"id": "q2", "title": "General Science", "category": "Science", "difficulty": "easy"},
    ]

    recs = recommender.recommend(
        weak_topics=weak_topics,
        strong_topics=strong_topics,
        available_quizzes=available_quizzes,
        excluded_categories={"geography"},
        recommended_difficulty="medium",
        limit=5,
    )

    # Excluded topic must never be recommended
    assert not any(r.category.lower() == "geography" for r in recs)


def test_recommend_strength_expansion():
    recommender = QuizRecommender()
    weak_topics = []
    strong_topics = [{"topic_id": "cat:physics", "topic_name": "Physics", "accuracy_pct": 92.0}]
    available_quizzes = [
        {"id": "q1", "title": "Quantum Physics Masterclass", "category": "Physics", "difficulty": "hard"},
    ]

    recs = recommender.recommend(
        weak_topics=weak_topics,
        strong_topics=strong_topics,
        available_quizzes=available_quizzes,
        excluded_categories=set(),
        recommended_difficulty="hard",
        limit=5,
    )

    assert len(recs) >= 1
    assert recs[0].category == "Physics"
    assert recs[0].reason_type == "strengthen_strong"

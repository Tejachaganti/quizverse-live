"""Unit tests for PerformanceIntelligenceAnalyzer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import pytest

from quizverse.intelligence.performance import PerformanceIntelligenceAnalyzer


def test_empty_history_performance():
    analyzer = PerformanceIntelligenceAnalyzer()
    analytics = analyzer.analyze_performance(attempts=[], window="recent")
    assert analytics["overall_accuracy_pct"] == 0.0
    assert analytics["total_attempts"] == 0
    assert analytics["avg_response_time_ms"] == 0
    assert "difficulty_breakdown" in analytics
    assert "mode_breakdown" in analytics


def test_performance_analytics_calculation():
    analyzer = PerformanceIntelligenceAnalyzer()
    now = datetime.now(UTC)

    attempts = [
        {
            "match_id": "m1",
            "mode": "classic",
            "difficulty": "medium",
            "correct_count": 8,
            "answer_count": 10,
            "final_score": 850,
            "response_time_ms": 2500,
            "outcome": "win",
            "completed_at": (now - timedelta(days=1)).isoformat(),
        },
        {
            "match_id": "m2",
            "mode": "speed",
            "difficulty": "hard",
            "correct_count": 6,
            "answer_count": 10,
            "final_score": 620,
            "response_time_ms": 1800,
            "outcome": "win",
            "completed_at": (now - timedelta(days=2)).isoformat(),
        },
        {
            "match_id": "m3",
            "mode": "practice",
            "difficulty": "easy",
            "correct_count": 10,
            "answer_count": 10,
            "final_score": 1000,
            "response_time_ms": 1500,
            "outcome": "win",
            "completed_at": (now - timedelta(days=3)).isoformat(),
        },
    ]

    analytics = analyzer.analyze_performance(attempts=attempts, window="7_days")
    assert analytics["total_attempts"] == 3
    assert analytics["total_correct"] == 24
    assert analytics["total_answered"] == 30
    assert analytics["overall_accuracy_pct"] == 80.0
    assert analytics["avg_score"] > 800

    diffs = analytics["difficulty_breakdown"]
    assert diffs["medium"]["attempts"] == 1
    assert diffs["medium"]["accuracy_pct"] == 80.0
    assert diffs["hard"]["attempts"] == 1
    assert diffs["hard"]["accuracy_pct"] == 60.0
    assert diffs["easy"]["attempts"] == 1
    assert diffs["easy"]["accuracy_pct"] == 100.0

    modes = analytics["mode_breakdown"]
    assert modes["competitive"]["matches"] == 2
    assert modes["competitive"]["wins"] == 2
    assert modes["practice"]["sessions"] == 1

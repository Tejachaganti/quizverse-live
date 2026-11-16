"""Unit tests for LearningReportGenerator."""

from __future__ import annotations

import pytest

from quizverse.intelligence.learning_reports import LearningReportGenerator


@pytest.mark.anyio
async def test_deterministic_learning_report_generation():
    generator = LearningReportGenerator(ai_service=None)
    performance_stats = {
        "overall_accuracy_pct": 76.5,
        "total_attempts": 25,
        "avg_response_time_ms": 3200,
    }
    weakness_summary = {
        "strong_topics": [{"topic_name": "Mathematics", "accuracy_pct": 92.0}],
        "weak_topics": [{"topic_name": "World History", "accuracy_pct": 42.0}],
        "needs_practice_topics": [{"topic_name": "Science", "accuracy_pct": 65.0}],
    }
    adaptive_diff = {
        "recommended_difficulty": "medium",
        "reasoning": "Consistent performance in medium quizzes.",
    }

    report = await generator.generate_report(
        player_name="TestPlayer",
        performance_stats=performance_stats,
        weakness_summary=weakness_summary,
        adaptive_difficulty=adaptive_diff,
        time_window="30_days",
    )

    assert report.generated_by == "deterministic_fallback"
    assert report.time_window == "30_days"
    assert "TestPlayer" in report.summary
    assert "Mathematics" in report.summary
    assert "World History" in report.summary

    data = report.data
    assert data["overall_accuracy_pct"] == 76.5
    assert data["strongest_topics"] == ["Mathematics"]
    assert data["weakest_topics"] == ["World History"]
    assert len(data["actionable_advice"]) >= 1
    assert any("World History" in a for a in data["actionable_advice"])

"""Weakness and topic intelligence detection engine with statistical confidence and trend tracking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class TopicAnalysis:
    topic_id: str
    topic_name: str
    attempts: int
    correct_count: int
    incorrect_count: int
    accuracy_pct: float
    avg_response_time_ms: int
    status: str  # "strong", "weak", "needs_practice", "improving", "declining", "insufficient_data"
    trend: str  # "improving", "declining", "stable"
    confidence: float  # 0.0 to 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "topic_name": self.topic_name,
            "attempts": self.attempts,
            "correct_count": self.correct_count,
            "incorrect_count": self.incorrect_count,
            "accuracy_pct": round(self.accuracy_pct, 1),
            "avg_response_time_ms": self.avg_response_time_ms,
            "status": self.status,
            "trend": self.trend,
            "confidence": round(self.confidence, 2),
        }


@dataclass(slots=True)
class WeaknessSummary:
    strong_topics: list[dict[str, Any]]
    weak_topics: list[dict[str, Any]]
    needs_practice_topics: list[dict[str, Any]]
    insufficient_data_topics: list[dict[str, Any]]
    total_topics_analyzed: int
    overall_accuracy_pct: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "strong_topics": self.strong_topics,
            "weak_topics": self.weak_topics,
            "needs_practice_topics": self.needs_practice_topics,
            "insufficient_data_topics": self.insufficient_data_topics,
            "total_topics_analyzed": self.total_topics_analyzed,
            "overall_accuracy_pct": round(self.overall_accuracy_pct, 1),
        }


class WeaknessDetector:
    """Analyzes player attempt history by category and topic to identify performance clusters."""

    MIN_SAMPLE_THRESHOLD = 3
    STRONG_ACCURACY_THRESHOLD = 75.0
    WEAK_ACCURACY_THRESHOLD = 50.0

    def analyze_topics(
        self,
        topic_attempts: dict[str, list[dict[str, Any]]],
        category_names: dict[str, str] | None = None,
    ) -> WeaknessSummary:
        names = category_names or {}
        analyzed_topics: list[TopicAnalysis] = []

        total_correct = 0
        total_answers = 0

        for topic_id, attempts in topic_attempts.items():
            if not attempts:
                continue

            topic_name = names.get(topic_id, topic_id.replace("category:", "").replace("-", " ").title())
            attempt_count = len(attempts)

            correct = sum(int(a.get("correct_count", 0) or 0) for a in attempts)
            answered = sum(int(a.get("answer_count", 0) or 0) for a in attempts)
            incorrect = max(0, answered - correct)

            total_correct += correct
            total_answers += answered

            accuracy = (correct / answered * 100.0) if answered > 0 else 0.0

            # Calculate average response time if available
            response_times = [int(a.get("response_time_ms", 0)) for a in attempts if int(a.get("response_time_ms", 0)) > 0]
            avg_rt = int(sum(response_times) / len(response_times)) if response_times else 0

            # Trend calculation: compare first half vs second half
            trend = "stable"
            if attempt_count >= 4:
                half = attempt_count // 2
                recent_half = attempts[:half]
                older_half = attempts[half:]

                rec_c = sum(int(a.get("correct_count", 0) or 0) for a in recent_half)
                rec_a = sum(int(a.get("answer_count", 0) or 0) for a in recent_half)
                old_c = sum(int(a.get("correct_count", 0) or 0) for a in older_half)
                old_a = sum(int(a.get("answer_count", 0) or 0) for a in older_half)

                rec_acc = (rec_c / rec_a * 100.0) if rec_a > 0 else accuracy
                old_acc = (old_c / old_a * 100.0) if old_a > 0 else accuracy

                if rec_acc >= old_acc + 15.0:
                    trend = "improving"
                elif rec_acc <= old_acc - 15.0:
                    trend = "declining"

            # Determine status based on minimum sample threshold
            if attempt_count < self.MIN_SAMPLE_THRESHOLD:
                status = "insufficient_data"
                confidence = min(0.5, attempt_count / self.MIN_SAMPLE_THRESHOLD * 0.5)
            elif accuracy >= self.STRONG_ACCURACY_THRESHOLD:
                status = "strong"
                confidence = min(1.0, 0.6 + (attempt_count / 15.0))
            elif accuracy < self.WEAK_ACCURACY_THRESHOLD:
                status = "weak"
                confidence = min(1.0, 0.6 + (attempt_count / 15.0))
            else:
                status = "needs_practice"
                confidence = min(1.0, 0.5 + (attempt_count / 20.0))

            analyzed_topics.append(
                TopicAnalysis(
                    topic_id=topic_id,
                    topic_name=topic_name,
                    attempts=attempt_count,
                    correct_count=correct,
                    incorrect_count=incorrect,
                    accuracy_pct=accuracy,
                    avg_response_time_ms=avg_rt,
                    status=status,
                    trend=trend,
                    confidence=confidence,
                )
            )

        # Categorize
        strong = [t.to_dict() for t in analyzed_topics if t.status == "strong"]
        weak = [t.to_dict() for t in analyzed_topics if t.status == "weak"]
        needs_practice = [t.to_dict() for t in analyzed_topics if t.status == "needs_practice"]
        insufficient = [t.to_dict() for t in analyzed_topics if t.status == "insufficient_data"]

        # Sort weak by lowest accuracy first, strong by highest
        weak.sort(key=lambda x: x["accuracy_pct"])
        strong.sort(key=lambda x: x["accuracy_pct"], reverse=True)
        needs_practice.sort(key=lambda x: x["accuracy_pct"])

        overall_acc = (total_correct / total_answers * 100.0) if total_answers > 0 else 0.0

        return WeaknessSummary(
            strong_topics=strong,
            weak_topics=weak,
            needs_practice_topics=needs_practice,
            insufficient_data_topics=insufficient,
            total_topics_analyzed=len(analyzed_topics),
            overall_accuracy_pct=overall_acc,
        )

"""Holistic AI learning report generator with robust deterministic fallback."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from quizverse.ai_generation import GenerationService


@dataclass(slots=True)
class LearningReportResult:
    summary: str
    report_type: str
    time_window: str
    data: dict[str, Any]
    generated_by: str  # "ai_assisted", "deterministic_fallback"

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "report_type": self.report_type,
            "time_window": self.time_window,
            "data": self.data,
            "generated_by": self.generated_by,
        }


class LearningReportGenerator:
    """Generates structured educational diagnostics and actionable study plans."""

    def __init__(self, ai_service: GenerationService | None = None) -> None:
        self._ai_service = ai_service

    async def generate_report(
        self,
        player_name: str,
        performance_stats: dict[str, Any],
        weakness_summary: dict[str, Any],
        adaptive_difficulty: dict[str, Any],
        time_window: str = "recent",
        report_type: str = "comprehensive",
    ) -> LearningReportResult:
        # Prepare deterministic data model first (the ground truth)
        strong = weakness_summary.get("strong_topics", [])
        weak = weakness_summary.get("weak_topics", [])
        needs_practice = weakness_summary.get("needs_practice_topics", [])
        acc = performance_stats.get("overall_accuracy_pct", 0.0)
        attempts = performance_stats.get("total_attempts", 0)
        rec_diff = adaptive_difficulty.get("recommended_difficulty", "medium")

        strong_names = [t.get("topic_name") for t in strong[:3]]
        weak_names = [t.get("topic_name") for t in weak[:3]]

        # Actionable advice generation
        actionable_advice = []
        if weak_names:
            actionable_advice.append(f"Dedicate your next 2-3 practice sessions exclusively to {', '.join(weak_names)} to convert foundational weaknesses into strengths.")
        if strong_names:
            actionable_advice.append(f"Leverage your mastery in {', '.join(strong_names)} to gain confidence; try tackling hard-tier quizzes in these domains.")
        if performance_stats.get("avg_response_time_ms", 0) > 8000:
            actionable_advice.append("Practice in Speed Challenge mode to lower your average response latency and sharpen split-second decision making.")
        else:
            actionable_advice.append("Maintain your current steady response pace while maintaining high precision.")

        actionable_advice.append(f"Work along your personalized learning path at the recommended '{rec_diff}' difficulty tier.")

        # Structured diagnostic payload
        diagnostic_data = {
            "player_name": player_name,
            "time_window": time_window,
            "overall_accuracy_pct": acc,
            "total_attempts": attempts,
            "strongest_topics": strong_names,
            "weakest_topics": weak_names,
            "needs_practice_topics": [t.get("topic_name") for t in needs_practice[:3]],
            "recommended_difficulty": rec_diff,
            "difficulty_reasoning": adaptive_difficulty.get("reasoning", ""),
            "improvement_areas": weak_names or ["Speed and timed endurance"],
            "speed_metrics": {
                "avg_response_time_ms": performance_stats.get("avg_response_time_ms", 0),
                "speed_rating": "Fast" if performance_stats.get("avg_response_time_ms", 0) < 5000 else "Standard",
            },
            "suggested_next_activities": [
                f"Practice drill in {weak_names[0]}" if weak_names else "General Knowledge Speed Drill",
                f"Mastery challenge at {rec_diff} difficulty",
                "Review recent incorrect answers in the question bank",
            ],
            "actionable_advice": actionable_advice,
        }

        # Deterministic summary
        deterministic_summary = (
            f"{player_name} has completed {attempts} verified sessions with an overall accuracy of {acc}%. "
            f"Strongest performance observed in {', '.join(strong_names) if strong_names else 'General topics'}. "
            f"Recommended focus area: {', '.join(weak_names) if weak_names else 'Advanced Speed Training'} at '{rec_diff}' difficulty."
        )

        # Attempt AI enhancement if enabled and configured
        if self._ai_service is not None and self._ai_service.enabled:
            try:
                ai_prompt = (
                    f"Generate a professional, encouraging 2-sentence educational learning report summary for {player_name}. "
                    f"Stats: Accuracy: {acc}%, Total sessions: {attempts}, Strong: {strong_names}, Weak: {weak_names}, "
                    f"Recommended difficulty: {rec_diff}. Never mention private keys or system details."
                )
                schema = {
                    "type": "OBJECT",
                    "properties": {
                        "executive_summary": {"type": "STRING"},
                        "personalized_encouragement": {"type": "STRING"},
                    },
                    "required": ["executive_summary", "personalized_encouragement"],
                }
                raw = await self._ai_service.provider.generate(ai_prompt, schema, max_output_tokens=300)
                parsed = json.loads(raw)
                ai_summary = f"{parsed.get('executive_summary', '')} {parsed.get('personalized_encouragement', '')}".strip()
                if len(ai_summary) > 20:
                    return LearningReportResult(
                        summary=ai_summary,
                        report_type=report_type,
                        time_window=time_window,
                        data=diagnostic_data,
                        generated_by="ai_assisted",
                    )
            except Exception:
                # Graceful fallback to deterministic report
                pass

        return LearningReportResult(
            summary=deterministic_summary,
            report_type=report_type,
            time_window=time_window,
            data=diagnostic_data,
            generated_by="deterministic_fallback",
        )

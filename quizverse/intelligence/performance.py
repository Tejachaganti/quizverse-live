"""Advanced performance intelligence service across customizable time windows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


class PerformanceIntelligenceAnalyzer:
    """Computes multidimensional performance statistics, velocity metrics, and streak tracking."""

    TIME_WINDOWS = {"recent", "7_days", "30_days", "all_time"}

    def filter_attempts_by_window(
        self,
        attempts: list[dict[str, Any]],
        window: str = "recent",
    ) -> list[dict[str, Any]]:
        if window not in self.TIME_WINDOWS:
            window = "recent"

        now = datetime.now(UTC)

        if window == "recent":
            return attempts[:10]
        elif window == "7_days":
            cutoff = now - timedelta(days=7)
            return [a for a in attempts if self._parse_date(a.get("completed_at")) >= cutoff]
        elif window == "30_days":
            cutoff = now - timedelta(days=30)
            return [a for a in attempts if self._parse_date(a.get("completed_at")) >= cutoff]
        else:
            return attempts

    @staticmethod
    def _parse_date(val: Any) -> datetime:
        if isinstance(val, datetime):
            return val if val.tzinfo else val.replace(tzinfo=UTC)
        if isinstance(val, str):
            try:
                dt = datetime.fromisoformat(val)
                return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
            except Exception:
                pass
        return datetime.min.replace(tzinfo=UTC)

    def analyze_performance(
        self,
        attempts: list[dict[str, Any]],
        window: str = "recent",
    ) -> dict[str, Any]:
        filtered = self.filter_attempts_by_window(attempts, window)

        total_attempts = len(filtered)
        if total_attempts == 0:
            return {
                "time_window": window,
                "total_attempts": 0,
                "total_correct": 0,
                "total_answered": 0,
                "overall_accuracy_pct": 0.0,
                "avg_score": 0.0,
                "avg_response_time_ms": 0,
                "difficulty_breakdown": {
                    "easy": {"attempts": 0, "correct": 0, "answered": 0, "accuracy_pct": 0.0},
                    "medium": {"attempts": 0, "correct": 0, "answered": 0, "accuracy_pct": 0.0},
                    "hard": {"attempts": 0, "correct": 0, "answered": 0, "accuracy_pct": 0.0},
                },
                "mode_breakdown": {
                    "competitive": {"matches": 0, "wins": 0, "accuracy_pct": 0.0},
                    "practice": {"sessions": 0, "accuracy_pct": 0.0},
                },
                "current_streak": {"type": "none", "count": 0},
                "best_performance": None,
            }

        total_correct = sum(int(a.get("correct_count", 0) or 0) for a in filtered)
        total_answered = sum(int(a.get("answer_count", 0) or 0) for a in filtered)
        overall_accuracy = (total_correct / total_answered * 100.0) if total_answered > 0 else 0.0

        scores = [int(a.get("final_score", 0) or 0) for a in filtered if a.get("final_score") is not None]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

        response_times = [int(a.get("response_time_ms", 0)) for a in filtered if int(a.get("response_time_ms", 0)) > 0]
        avg_rt = int(sum(response_times) / len(response_times)) if response_times else 0

        # Difficulty Breakdown
        diff_stats = {
            "easy": {"attempts": 0, "correct": 0, "answered": 0, "accuracy_pct": 0.0},
            "medium": {"attempts": 0, "correct": 0, "answered": 0, "accuracy_pct": 0.0},
            "hard": {"attempts": 0, "correct": 0, "answered": 0, "accuracy_pct": 0.0},
        }

        for a in filtered:
            diff = (a.get("difficulty") or "medium").lower()
            if diff not in diff_stats:
                diff = "medium"
            c = int(a.get("correct_count", 0) or 0)
            ans = int(a.get("answer_count", 0) or 0)
            diff_stats[diff]["attempts"] += 1
            diff_stats[diff]["correct"] += c
            diff_stats[diff]["answered"] += ans

        for diff, s in diff_stats.items():
            s["accuracy_pct"] = round((s["correct"] / s["answered"] * 100.0), 1) if s["answered"] > 0 else 0.0

        # Mode Breakdown (Competitive vs Practice)
        comp_matches = [a for a in filtered if a.get("mode") not in {"solo_practice", "practice"}]
        prac_matches = [a for a in filtered if a.get("mode") in {"solo_practice", "practice"}]

        comp_wins = sum(1 for a in comp_matches if a.get("outcome") == "win")
        comp_c = sum(int(a.get("correct_count", 0) or 0) for a in comp_matches)
        comp_a = sum(int(a.get("answer_count", 0) or 0) for a in comp_matches)
        comp_acc = round(comp_c / comp_a * 100.0, 1) if comp_a > 0 else 0.0

        prac_c = sum(int(a.get("correct_count", 0) or 0) for a in prac_matches)
        prac_a = sum(int(a.get("answer_count", 0) or 0) for a in prac_matches)
        prac_acc = round(prac_c / prac_a * 100.0, 1) if prac_a > 0 else 0.0

        # Best Performance
        best_match = None
        if filtered:
            sorted_by_score = sorted(filtered, key=lambda x: int(x.get("final_score", 0) or 0), reverse=True)
            top = sorted_by_score[0]
            best_match = {
                "match_id": top.get("match_id"),
                "mode": top.get("mode"),
                "score": top.get("final_score"),
                "accuracy_pct": round(int(top.get("correct_count", 0)) / max(1, int(top.get("answer_count", 1))) * 100.0, 1),
                "completed_at": top.get("completed_at"),
            }

        # Current Win/Play Streak
        streak_count = 0
        streak_type = "none"
        if filtered:
            first_outcome = filtered[0].get("outcome", "none")
            streak_type = "win" if first_outcome == "win" else "play"
            for a in filtered:
                if (streak_type == "win" and a.get("outcome") == "win") or (streak_type == "play"):
                    streak_count += 1
                else:
                    break

        return {
            "time_window": window,
            "total_attempts": total_attempts,
            "total_correct": total_correct,
            "total_answered": total_answered,
            "overall_accuracy_pct": round(overall_accuracy, 1),
            "avg_score": avg_score,
            "avg_response_time_ms": avg_rt,
            "difficulty_breakdown": diff_stats,
            "mode_breakdown": {
                "competitive": {"matches": len(comp_matches), "wins": comp_wins, "accuracy_pct": comp_acc},
                "practice": {"sessions": len(prac_matches), "accuracy_pct": prac_acc},
            },
            "current_streak": {"type": streak_type, "count": streak_count},
            "best_performance": best_match,
        }

"""Adaptive difficulty engine with minimum evidence thresholds and explainable recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AdaptiveDifficultyResult:
    recommended_difficulty: str  # "easy", "medium", "hard"
    current_difficulty: str
    confidence: float  # 0.0 to 1.0
    evidence_count: int
    reasoning: str
    adjustment_direction: str  # "increase", "decrease", "maintain"

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended_difficulty": self.recommended_difficulty,
            "current_difficulty": self.current_difficulty,
            "confidence": round(self.confidence, 2),
            "evidence_count": self.evidence_count,
            "reasoning": self.reasoning,
            "adjustment_direction": self.adjustment_direction,
        }


class AdaptiveDifficultyEngine:
    """Calculates personalized difficulty adjustments grounded in validated player attempts."""

    DIFFICULTY_TIERS = ["easy", "medium", "hard"]
    MIN_EVIDENCE_THRESHOLD = 3
    PROMOTION_ACCURACY_THRESHOLD = 80.0
    DEMOTION_ACCURACY_THRESHOLD = 50.0

    def evaluate(
        self,
        current_difficulty: str,
        attempts: list[dict[str, Any]],
    ) -> AdaptiveDifficultyResult:
        curr = (current_difficulty or "medium").lower()
        if curr not in self.DIFFICULTY_TIERS:
            curr = "medium"

        evidence_count = len(attempts)

        # 1. Check for insufficient evidence
        if evidence_count < self.MIN_EVIDENCE_THRESHOLD:
            return AdaptiveDifficultyResult(
                recommended_difficulty=curr,
                current_difficulty=curr,
                confidence=round(evidence_count / self.MIN_EVIDENCE_THRESHOLD * 0.5, 2),
                evidence_count=evidence_count,
                reasoning=(
                    f"Insufficient play history ({evidence_count}/{self.MIN_EVIDENCE_THRESHOLD} required attempts). "
                    f"Maintaining current '{curr}' difficulty until more evidence is recorded."
                ),
                adjustment_direction="maintain",
            )

        # 2. Compute aggregate metrics
        total_correct = sum(int(a.get("correct_count", 0) or 0) for a in attempts)
        total_answered = sum(int(a.get("answer_count", 0) or 0) for a in attempts)

        if total_answered == 0:
            return AdaptiveDifficultyResult(
                recommended_difficulty=curr,
                current_difficulty=curr,
                confidence=0.0,
                evidence_count=evidence_count,
                reasoning="Attempts contain no recorded answers; maintaining difficulty.",
                adjustment_direction="maintain",
            )

        accuracy = (total_correct / total_answered) * 100.0

        # Recent trend (last 3 matches vs older)
        recent = attempts[:3]
        recent_correct = sum(int(a.get("correct_count", 0) or 0) for a in recent)
        recent_answered = sum(int(a.get("answer_count", 0) or 0) for a in recent)
        recent_accuracy = (recent_correct / recent_answered * 100.0) if recent_answered > 0 else accuracy

        # Confidence increases with sample size (up to 10 attempts = 1.0)
        confidence = min(1.0, 0.6 + (evidence_count / 20.0))

        # 3. Decision Matrix
        curr_index = self.DIFFICULTY_TIERS.index(curr)

        # High performance: Check for promotion
        if accuracy >= self.PROMOTION_ACCURACY_THRESHOLD and recent_accuracy >= 75.0:
            if curr_index < len(self.DIFFICULTY_TIERS) - 1:
                promoted = self.DIFFICULTY_TIERS[curr_index + 1]
                return AdaptiveDifficultyResult(
                    recommended_difficulty=promoted,
                    current_difficulty=curr,
                    confidence=confidence,
                    evidence_count=evidence_count,
                    reasoning=(
                        f"Outstanding performance: You achieved {round(accuracy, 1)}% accuracy over {evidence_count} attempts "
                        f"at '{curr}' difficulty. Recommended stepping up to '{promoted}' for an optimal learning curve."
                    ),
                    adjustment_direction="increase",
                )
            else:
                return AdaptiveDifficultyResult(
                    recommended_difficulty=curr,
                    current_difficulty=curr,
                    confidence=confidence,
                    evidence_count=evidence_count,
                    reasoning=(
                        f"Mastery reached: You maintain {round(accuracy, 1)}% accuracy at the highest difficulty tier ('hard'). "
                        "Keep playing at 'hard' to sharpen your competitive edge!"
                    ),
                    adjustment_direction="maintain",
                )

        # Low performance: Check for demotion
        if accuracy < self.DEMOTION_ACCURACY_THRESHOLD and recent_accuracy < 55.0:
            if curr_index > 0:
                demoted = self.DIFFICULTY_TIERS[curr_index - 1]
                return AdaptiveDifficultyResult(
                    recommended_difficulty=demoted,
                    current_difficulty=curr,
                    confidence=confidence,
                    evidence_count=evidence_count,
                    reasoning=(
                        f"Targeted reinforcement recommended: Accuracy is currently {round(accuracy, 1)}% at '{curr}' difficulty. "
                        f"Stepping down to '{demoted}' will help solidify foundational concepts."
                    ),
                    adjustment_direction="decrease",
                )
            else:
                return AdaptiveDifficultyResult(
                    recommended_difficulty=curr,
                    current_difficulty=curr,
                    confidence=confidence,
                    evidence_count=evidence_count,
                    reasoning=(
                        f"Accuracy is {round(accuracy, 1)}% on '{curr}' difficulty. "
                        "Continuing with practice drills at 'easy' will help build knowledge and speed."
                    ),
                    adjustment_direction="maintain",
                )

        # Moderate / Mixed performance: Maintain
        return AdaptiveDifficultyResult(
            recommended_difficulty=curr,
            current_difficulty=curr,
            confidence=confidence,
            evidence_count=evidence_count,
            reasoning=(
                f"Balanced performance: Your accuracy is steady at {round(accuracy, 1)}% across {evidence_count} attempts. "
                f"Current '{curr}' difficulty provides the right level of intellectual challenge."
            ),
            adjustment_direction="maintain",
        )

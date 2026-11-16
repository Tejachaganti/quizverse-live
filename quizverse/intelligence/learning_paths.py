"""Personalized learning path generator and curriculum progression engine."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class PathStepSpec:
    step_number: int
    title: str
    description: str
    activity_type: str  # "quiz_practice", "topic_deep_dive", "speed_drill", "exam_prep", "revision"
    target_category: str | None = None
    target_quiz_id: str | None = None
    target_difficulty: str | None = None
    estimated_minutes: int = 10
    is_completed: bool = False
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_number": self.step_number,
            "title": self.title,
            "description": self.description,
            "activity_type": self.activity_type,
            "target_category": self.target_category,
            "target_quiz_id": self.target_quiz_id,
            "target_difficulty": self.target_difficulty,
            "estimated_minutes": self.estimated_minutes,
            "is_completed": self.is_completed,
            "completed_at": self.completed_at,
        }


@dataclass(slots=True)
class GeneratedLearningPath:
    title: str
    objective: str
    target_category: str
    target_subcategory: str | None
    recommended_difficulty: str
    steps: list[PathStepSpec] = field(default_factory=list)
    estimated_minutes: int = 40
    completion_criteria: str = "Complete all practice activities and achieve at least 70% accuracy."

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "objective": self.objective,
            "target_category": self.target_category,
            "target_subcategory": self.target_subcategory,
            "recommended_difficulty": self.recommended_difficulty,
            "steps": [s.to_dict() for s in self.steps],
            "estimated_minutes": self.estimated_minutes,
            "completion_criteria": self.completion_criteria,
        }


class LearningPathGenerator:
    """Constructs structured, personalized educational roadmaps."""

    def generate_path(
        self,
        weak_topics: list[dict[str, Any]],
        strong_topics: list[dict[str, Any]],
        preferences: dict[str, Any],
        available_quizzes: list[dict[str, Any]] | None = None,
        recommended_difficulty: str = "medium",
    ) -> GeneratedLearningPath:
        quizzes = available_quizzes or []

        # 1. Choose target category based on top weak topic or user preferred topic
        target_category = "General Knowledge"
        target_subcat = None

        if weak_topics:
            top_weak = weak_topics[0]
            target_category = top_weak.get("topic_name") or top_weak.get("topic_id", "General Knowledge")
        elif preferences.get("topics"):
            # Find an increased topic
            preferred = [t for t, pref in preferences["topics"].items() if pref == "increase"]
            if preferred:
                target_category = preferred[0].replace("category:", "").replace("-", " ").title()

        diff = (recommended_difficulty or "medium").lower()
        if diff not in {"easy", "medium", "hard"}:
            diff = "medium"

        # 2. Find relevant quizzes in available quiz bank
        relevant_quizzes = [
            q for q in quizzes
            if target_category.lower() in str(q.get("category", "")).lower()
            or target_category.lower() in str(q.get("title", "")).lower()
        ]

        quiz_1 = relevant_quizzes[0]["id"] if len(relevant_quizzes) > 0 else None
        quiz_2 = relevant_quizzes[1]["id"] if len(relevant_quizzes) > 1 else quiz_1

        # 3. Create structured step sequence
        steps = [
            PathStepSpec(
                step_number=1,
                title=f"Step 1: Foundational Review in {target_category}",
                description=f"Begin with focused practice drills covering fundamental concepts in {target_category}.",
                activity_type="topic_deep_dive",
                target_category=target_category,
                target_quiz_id=quiz_1,
                target_difficulty="easy" if diff == "hard" else diff,
                estimated_minutes=10,
            ),
            PathStepSpec(
                step_number=2,
                title=f"Step 2: Core Practice Drill",
                description=f"Test comprehension with standard multiple-choice questions at your recommended {diff} difficulty.",
                activity_type="quiz_practice",
                target_category=target_category,
                target_quiz_id=quiz_2,
                target_difficulty=diff,
                estimated_minutes=15,
            ),
            PathStepSpec(
                step_number=3,
                title=f"Step 3: Speed & Rapid Recall Drill",
                description=f"Sharpen reaction time and instinct with a rapid timed challenge in {target_category}.",
                activity_type="speed_drill",
                target_category=target_category,
                target_quiz_id=quiz_1,
                target_difficulty=diff,
                estimated_minutes=10,
            ),
            PathStepSpec(
                step_number=4,
                title=f"Step 4: Mastery Challenge",
                description=f"Achieve final mastery with comprehensive problem-solving and higher difficulty questions.",
                activity_type="exam_prep",
                target_category=target_category,
                target_quiz_id=quiz_2,
                target_difficulty="hard" if diff != "easy" else "medium",
                estimated_minutes=15,
            ),
        ]

        total_minutes = sum(s.estimated_minutes for s in steps)

        return GeneratedLearningPath(
            title=f"Personalized Learning Path: {target_category} Mastery",
            objective=(
                f"Elevate your accuracy and competitive proficiency in {target_category} through progressive "
                f"reinforcement, speed training, and mastery evaluation."
            ),
            target_category=target_category,
            target_subcategory=target_subcat,
            recommended_difficulty=diff,
            steps=steps,
            estimated_minutes=total_minutes,
            completion_criteria="Complete all four practice steps with verified answers to graduate this learning path.",
        )

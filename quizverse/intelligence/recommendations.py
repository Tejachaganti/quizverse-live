"""Intelligent quiz recommendation engine with pedagogical rationale mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class QuizRecommendation:
    quiz_id: str | None
    quiz_title: str
    category: str
    difficulty: str
    reason_type: str  # "weak_topic", "strengthen_strong", "improve_speed", "exam_prep", "new_topic", "revision"
    reason_text: str
    score: float  # Priority ranking 0.0 to 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "quiz_id": self.quiz_id,
            "quiz_title": self.quiz_title,
            "category": self.category,
            "difficulty": self.difficulty,
            "reason_type": self.reason_type,
            "reason_text": self.reason_text,
            "score": round(self.score, 2),
        }


class QuizRecommender:
    """Generates personalized, explainable quiz recommendations across distinct learning strategies."""

    def recommend(
        self,
        weak_topics: list[dict[str, Any]],
        strong_topics: list[dict[str, Any]],
        available_quizzes: list[dict[str, Any]],
        excluded_categories: set[str],
        active_exam_collection: str | None = None,
        recommended_difficulty: str = "medium",
        limit: int = 6,
    ) -> list[QuizRecommendation]:
        recommendations: list[QuizRecommendation] = []
        seen_quiz_ids: set[str] = set()

        def is_eligible(q: dict[str, Any]) -> bool:
            cat = str(q.get("category", "")).lower()
            if any(exc.lower() in cat for exc in excluded_categories):
                return False
            # Check review status: do not recommend rejected
            if q.get("review_status") == "rejected":
                return False
            return True

        eligible_quizzes = [q for q in available_quizzes if is_eligible(q)]

        # 1. Strategy A: Practice Weak Topics (Top Priority)
        for weak in weak_topics:
            w_name = weak.get("topic_name", "")
            if any(exc.lower() in w_name.lower() for exc in excluded_categories):
                continue
            matches = [
                q for q in eligible_quizzes
                if q["id"] not in seen_quiz_ids
                and (w_name.lower() in str(q.get("category", "")).lower() or w_name.lower() in str(q.get("title", "")).lower())
            ]
            if matches:
                chosen = matches[0]
                seen_quiz_ids.add(chosen["id"])
                recommendations.append(
                    QuizRecommendation(
                        quiz_id=chosen["id"],
                        quiz_title=chosen["title"],
                        category=chosen.get("category", w_name),
                        difficulty=chosen.get("difficulty", "medium"),
                        reason_type="weak_topic",
                        reason_text=f"Your accuracy in {w_name} is currently {round(weak.get('accuracy_pct', 0))}% — targeted practice will turn this into a strength.",
                        score=0.95,
                    )
                )
                break
            else:
                # Built-in practice recommendation for weak topic
                recommendations.append(
                    QuizRecommendation(
                        quiz_id=None,
                        quiz_title=f"{w_name} Practice Drill",
                        category=w_name,
                        difficulty=recommended_difficulty,
                        reason_type="weak_topic",
                        reason_text=f"Focus your next practice round on {w_name} to strengthen your foundational recall.",
                        score=0.90,
                    )
                )
                break

        # 2. Strategy B: Strengthen Strong Topics (Push to Higher Difficulty)
        for strong in strong_topics:
            s_name = strong.get("topic_name", "")
            if any(exc.lower() in s_name.lower() for exc in excluded_categories):
                continue
            matches = [
                q for q in eligible_quizzes
                if q["id"] not in seen_quiz_ids
                and (s_name.lower() in str(q.get("category", "")).lower() or s_name.lower() in str(q.get("title", "")).lower())
            ]
            if matches:
                chosen = matches[0]
                seen_quiz_ids.add(chosen["id"])
                recommendations.append(
                    QuizRecommendation(
                        quiz_id=chosen["id"],
                        quiz_title=chosen["title"],
                        category=chosen.get("category", s_name),
                        difficulty="hard" if recommended_difficulty != "easy" else "medium",
                        reason_type="strengthen_strong",
                        reason_text=f"You excel in {s_name} with {round(strong.get('accuracy_pct', 0))}% accuracy! Test your limits on an advanced quiz.",
                        score=0.85,
                    )
                )
                break

        # 3. Strategy C: Exam Preparation (if exam collection active)
        if active_exam_collection:
            exam_matches = [
                q for q in eligible_quizzes
                if q["id"] not in seen_quiz_ids
                and (active_exam_collection.lower() in str(q.get("category", "")).lower() or active_exam_collection.lower() in str(q.get("title", "")).lower())
            ]
            if exam_matches:
                chosen = exam_matches[0]
                seen_quiz_ids.add(chosen["id"])
                recommendations.append(
                    QuizRecommendation(
                        quiz_id=chosen["id"],
                        quiz_title=chosen["title"],
                        category=chosen.get("category", active_exam_collection),
                        difficulty=chosen.get("difficulty", "medium"),
                        reason_type="exam_prep",
                        reason_text=f"Curated syllabus practice aligned with your target exam collection: {active_exam_collection}.",
                        score=0.88,
                    )
                )

        # 4. Strategy D: Speed & Rapid Recall Drill
        speed_candidates = [q for q in eligible_quizzes if q["id"] not in seen_quiz_ids]
        if speed_candidates:
            chosen = speed_candidates[0]
            seen_quiz_ids.add(chosen["id"])
            recommendations.append(
                QuizRecommendation(
                    quiz_id=chosen["id"],
                    quiz_title=chosen["title"],
                    category=chosen.get("category", "General"),
                    difficulty=chosen.get("difficulty", "medium"),
                    reason_type="improve_speed",
                    reason_text="Sharpen rapid decision making and reaction speed with this timed knowledge challenge.",
                    score=0.80,
                )
            )

        # 5. Strategy E: Explore a New Topic
        attempted_cats = {t.get("topic_name", "").lower() for t in weak_topics + strong_topics}
        new_topic_quizzes = [
            q for q in eligible_quizzes
            if q["id"] not in seen_quiz_ids and str(q.get("category", "")).lower() not in attempted_cats
        ]
        if new_topic_quizzes:
            chosen = new_topic_quizzes[0]
            seen_quiz_ids.add(chosen["id"])
            recommendations.append(
                QuizRecommendation(
                    quiz_id=chosen["id"],
                    quiz_title=chosen["title"],
                    category=chosen.get("category", "Exploration"),
                    difficulty="easy",
                    reason_type="new_topic",
                    reason_text=f"Broaden your intellectual repertoire by trying a brand new topic in {chosen.get('category')}.",
                    score=0.75,
                )
            )

        # 6. Fallback general recommendations if list is short
        for q in eligible_quizzes:
            if len(recommendations) >= limit:
                break
            if q["id"] not in seen_quiz_ids:
                seen_quiz_ids.add(q["id"])
                recommendations.append(
                    QuizRecommendation(
                        quiz_id=q["id"],
                        quiz_title=q["title"],
                        category=q.get("category", "General"),
                        difficulty=q.get("difficulty", "medium"),
                        reason_type="revision",
                        reason_text="Review fundamental principles and maintain your active daily learning streak.",
                        score=0.70,
                    )
                )

        # If no quizzes at all are available, provide standard practice prompts
        if not recommendations:
            recommendations.append(
                QuizRecommendation(
                    quiz_id=None,
                    quiz_title="General Knowledge Practice Drill",
                    category="General Knowledge",
                    difficulty="medium",
                    reason_type="revision",
                    reason_text="Sharpen everyday cognitive recall with built-in general knowledge practice.",
                    score=0.70,
                )
            )

        return recommendations[:limit]

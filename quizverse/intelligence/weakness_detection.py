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

    def analyze_session_review(
        self,
        review_data: dict[str, Any] | None,
        mode: str | None = None,
        extra_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Analyzes a completed game session review to detect strengths, weaknesses, and topic performance."""
        if not review_data or not isinstance(review_data, dict):
            return None

        ctx = extra_context or {}
        active_mode = str(mode or review_data.get("summary", {}).get("mode", "classic")).lower()
        questions = review_data.get("questions", [])
        if not isinstance(questions, list):
            questions = []

        total_questions = len(questions)

        # -------------------------------------------------------------
        # 1. Specialized Mode: Escape Room
        # -------------------------------------------------------------
        if active_mode == "escape_room":
            solved_puzzles = sum(1 for q in questions if q.get("status") == "correct")
            hints_used = int(ctx.get("hints_used", 0))
            hints_remaining = int(ctx.get("hints_remaining", 3))
            elapsed_seconds = int(ctx.get("elapsed_seconds", 0))
            outcome = str(ctx.get("outcome", "victory" if solved_puzzles >= 10 else "failure"))

            return {
                "summary": {
                    "total_questions": total_questions,
                    "questions_attempted": solved_puzzles,
                    "correct_answers": solved_puzzles,
                    "incorrect_answers": 0,
                    "unanswered_questions": total_questions - solved_puzzles,
                    "accuracy_percent": None,
                    "score": None,
                    "completion_status": "Room Escaped (Victory)" if outcome == "victory" else "Time Expired (Incomplete)",
                    "accuracy_formula": "Standard multiple-choice accuracy is not applicable to cooperative room escape puzzles.",
                    "mode": "escape_room",
                    "mode_label": "Escape Room",
                },
                "escape_progress": {
                    "solved_puzzles": solved_puzzles,
                    "total_puzzles": total_questions,
                    "completed_rooms": solved_puzzles // 2,
                    "total_rooms": 5,
                    "hints_used": hints_used,
                    "hints_remaining": hints_remaining,
                    "elapsed_seconds": elapsed_seconds,
                    "outcome": outcome,
                },
                "topics": [],
                "strengths": [],
                "weaknesses": [],
                "improving": [],
                "insufficient_data": [],
                "repeated_mistakes": [],
                "recommendations": [
                    "Escape Room challenges evaluate cooperative teamwork and logic ciphers rather than individual subject accuracy.",
                    f"Session record: {solved_puzzles}/{total_questions} puzzle locks solved with {hints_used} hint{'s' if hints_used != 1 else ''} consumed.",
                ],
                "mode_notes": "Escape Room is a cooperative cipher-solving challenge. Multiple-choice topic classification is excluded.",
                "historical_context": {
                    "is_available": False,
                    "note": "Historical progress tracking belongs to a later phase for cooperative rooms.",
                },
            }

        # -------------------------------------------------------------
        # 2. Specialized Mode: Bluff Master
        # -------------------------------------------------------------
        if active_mode == "bluff_master":
            bluff_summary = review_data.get("summary", {})
            bluffs_submitted = int(ctx.get("bluffs_submitted", sum(1 for q in questions if q.get("submitted_text"))))
            fooled_count = int(ctx.get("fooled_players_count", bluff_summary.get("fooled_players_count", 0)))
            correct_truths = int(bluff_summary.get("correct_answers", sum(1 for q in questions if q.get("status") == "correct")))
            incorrect_votes = int(bluff_summary.get("incorrect_answers", sum(1 for q in questions if q.get("status") == "incorrect")))
            unanswered_votes = int(bluff_summary.get("unanswered_questions", sum(1 for q in questions if q.get("status") == "unanswered")))
            total_votes_cast = correct_truths + incorrect_votes

            return {
                "summary": {
                    "total_questions": total_questions,
                    "questions_attempted": total_votes_cast,
                    "correct_answers": correct_truths,
                    "incorrect_answers": incorrect_votes,
                    "unanswered_questions": unanswered_votes,
                    "accuracy_percent": None,
                    "score": ctx.get("player_score", bluff_summary.get("score")),
                    "completion_status": "Bluff Match Completed",
                    "accuracy_formula": "Standard knowledge accuracy is not applicable to social deception and bluff voting.",
                    "mode": "bluff_master",
                    "mode_label": "Bluff Master",
                },
                "bluff_performance": {
                    "bluffs_submitted": bluffs_submitted,
                    "fooled_players_count": fooled_count,
                    "correct_truths_voted": correct_truths,
                    "fooled_votes_cast": incorrect_votes,
                },
                "topics": [],
                "strengths": [],
                "weaknesses": [],
                "improving": [],
                "insufficient_data": [],
                "repeated_mistakes": [],
                "recommendations": [
                    "Bluff Master scores reflect psychological deception and seeing through opponents' fake answers.",
                    f"Deception effectiveness: Fooled opponents {fooled_count} time{'s' if fooled_count != 1 else ''} and correctly detected {correct_truths} truth{'s' if correct_truths != 1 else ''}.",
                ],
                "mode_notes": "Bluff Master evaluates deception and social deduction. Categorized academic accuracy does not apply.",
                "historical_context": {
                    "is_available": False,
                    "note": "Deception statistics are calculated per match session; historical academic topic tracking is excluded.",
                },
            }

        # -------------------------------------------------------------
        # 3. Standard Knowledge Modes (Classic, Speed, Team, Elimination, Solo, Royale, Power Play)
        # -------------------------------------------------------------
        correct_count = sum(1 for q in questions if q.get("status") == "correct")
        incorrect_count = sum(1 for q in questions if q.get("status") == "incorrect")
        unanswered_count = sum(1 for q in questions if q.get("status") in ("unanswered", "eliminated"))

        # Accurately based on valid answered questions
        questions_attempted = correct_count + incorrect_count
        if questions_attempted > 0:
            overall_accuracy = round((correct_count / questions_attempted) * 100.0, 1)
        else:
            overall_accuracy = 0.0

        # Topic Grouping
        topic_map: dict[str, list[dict[str, Any]]] = {}
        for q in questions:
            cat = str(q.get("category") or "").strip()
            if not cat:
                cat = "Topic information unavailable"
            topic_map.setdefault(cat, []).append(q)

        topics: list[dict[str, Any]] = []
        for topic_name, t_questions in topic_map.items():
            t_total = len(t_questions)
            t_correct = sum(1 for q in t_questions if q.get("status") == "correct")
            t_incorrect = sum(1 for q in t_questions if q.get("status") == "incorrect")
            t_unanswered = sum(1 for q in t_questions if q.get("status") in ("unanswered", "eliminated"))
            t_attempted = t_correct + t_incorrect

            if t_attempted > 0:
                t_acc = round((t_correct / t_attempted) * 100.0, 1)
            else:
                t_acc = 0.0

            # Transparent classification thresholds:
            # - Insufficient Data: < 2 questions attempted in this session
            # - Strong: >= 75.0% accuracy (with 2+ attempted)
            # - Improving: >= 50.0% and < 75.0% accuracy (with 2+ attempted)
            # - Needs Practice: < 50.0% accuracy (with 2+ attempted)
            if t_attempted < 2:
                classification = "Insufficient Data"
                if t_attempted == 1:
                    reason = f"Only 1 question attempted ({t_correct}/1 correct); minimum 2 needed to establish topic trend."
                else:
                    reason = "No questions attempted in this topic (all unanswered or timed out)."
            elif t_acc >= 75.0:
                classification = "Strong"
                reason = f"{t_acc}% accuracy ({t_correct}/{t_attempted} correct)."
            elif t_acc >= 50.0:
                classification = "Improving"
                reason = f"{t_acc}% accuracy ({t_correct}/{t_attempted} correct)."
            else:
                classification = "Needs Practice"
                reason = f"{t_acc}% accuracy ({t_correct}/{t_attempted} correct, {t_incorrect} missed)."

            topics.append({
                "topic_name": topic_name,
                "questions_total": t_total,
                "questions_attempted": t_attempted,
                "correct_answers": t_correct,
                "incorrect_answers": t_incorrect,
                "unanswered_questions": t_unanswered,
                "accuracy_percent": t_acc,
                "classification": classification,
                "reason": reason,
            })

        # Sort topics: alphabetical by name for standard display
        topics.sort(key=lambda t: t["topic_name"].lower())

        # Clusters
        strong_topics = [t for t in topics if t["classification"] == "Strong"]
        strong_topics.sort(key=lambda x: (x["accuracy_percent"], x["questions_attempted"]), reverse=True)

        weak_topics = [t for t in topics if t["classification"] == "Needs Practice"]
        weak_topics.sort(key=lambda x: (x["accuracy_percent"], -x["incorrect_answers"]))

        improving_topics = [t for t in topics if t["classification"] == "Improving"]
        improving_topics.sort(key=lambda x: (x["accuracy_percent"], x["questions_attempted"]), reverse=True)

        insufficient_data_topics = [t for t in topics if t["classification"] == "Insufficient Data"]
        insufficient_data_topics.sort(key=lambda x: x["questions_total"], reverse=True)

        # Repeated Mistakes Detection: topics where >= 2 questions were missed
        repeated_mistakes: list[dict[str, Any]] = []
        for t in topics:
            if t["incorrect_answers"] >= 2:
                missed = [
                    {"number": q.get("number"), "prompt": q.get("prompt")}
                    for q in topic_map[t["topic_name"]]
                    if q.get("status") == "incorrect"
                ]
                repeated_mistakes.append({
                    "topic_name": t["topic_name"],
                    "mistake_count": t["incorrect_answers"],
                    "missed_questions": missed,
                    "reason": f"{t['incorrect_answers']} mistakes in {t['topic_name']}.",
                })
        repeated_mistakes.sort(key=lambda x: x["mistake_count"], reverse=True)

        # Evidence-Based Recommendations
        recommendations: list[str] = []
        for w in weak_topics:
            recommendations.append(
                f"Prioritize practice in {w['topic_name']}: {w['correct_answers']}/{w['questions_attempted']} correct ({w['accuracy_percent']}% accuracy)."
            )
        for rm in repeated_mistakes:
            recommendations.append(
                f"Review foundational concepts in {rm['topic_name']} ({rm['mistake_count']} missed questions in this session)."
            )
        for imp in improving_topics:
            recommendations.append(
                f"Continue practice in {imp['topic_name']} to build consistency ({imp['accuracy_percent']}% accuracy)."
            )
        for s in strong_topics:
            recommendations.append(
                f"Maintain mastery in {s['topic_name']} ({s['accuracy_percent']}% accuracy across {s['questions_attempted']} questions)."
            )
        if questions_attempted == 0:
            recommendations.append(
                "Session completed with no answered questions. Try locking in answers before the countdown expires to evaluate your topic strengths."
            )
        elif not weak_topics and strong_topics:
            recommendations.append(
                "Outstanding session! High accuracy achieved across all evaluated topics."
            )

        # Derive completion status
        if active_mode == "solo_practice":
            completion_status = "Practice Complete"
        elif active_mode == "elimination":
            elim = bool(ctx.get("eliminated", False))
            elim_round = ctx.get("eliminated_round")
            completion_status = f"Eliminated in Round {elim_round}" if elim and elim_round else ("Sole Survivor (Victory)" if not elim else "Elimination Match Complete")
        elif active_mode == "team_battle":
            outcome = ctx.get("team_outcome", "draw")
            completion_status = f"Team Battle ({outcome.title()})"
        else:
            completion_status = "Match Complete"

        res: dict[str, Any] = {
            "summary": {
                "total_questions": total_questions,
                "questions_attempted": questions_attempted,
                "correct_answers": correct_count,
                "incorrect_answers": incorrect_count,
                "unanswered_questions": unanswered_count,
                "accuracy_percent": overall_accuracy,
                "score": ctx.get("player_score", review_data.get("summary", {}).get("score")),
                "completion_status": completion_status,
                "accuracy_formula": "accuracy_percent = (correct_answers / questions_attempted) * 100; unanswered questions are reported separately and excluded from the denominator.",
                "mode": active_mode,
                "mode_label": active_mode.replace("_", " ").title(),
            },
            "topics": topics,
            "strengths": strong_topics,
            "weaknesses": weak_topics,
            "improving": improving_topics,
            "insufficient_data": insufficient_data_topics,
            "repeated_mistakes": repeated_mistakes,
            "recommendations": recommendations,
            "mode_notes": None,
            "historical_context": {
                "is_available": bool(ctx.get("has_profile", False) and ctx.get("historical_matches_count", 0) >= 2),
                "note": "Historical trend tracking requires an authenticated profile with 2+ completed matches. This report displays verified performance for your current match.",
            },
        }

        # Attach mode-specific detail objects
        if active_mode == "team_battle" and "team_id" in ctx:
            res["team_details"] = {
                "team_id": ctx.get("team_id"),
                "team_name": "Team Violet" if ctx.get("team_id") == "violet" else "Team Cyan",
                "player_score": ctx.get("player_score", 0),
                "team_score": ctx.get("team_score", 0),
                "opposing_team_name": ctx.get("opposing_team_name", "Opposing Team"),
                "opposing_team_score": ctx.get("opposing_team_score", 0),
                "team_outcome": ctx.get("team_outcome", "draw"),
                "individual_note": "Individual metrics reflect your personal answers; team score reflects collective team performance.",
            }
        elif active_mode == "elimination":
            res["elimination_details"] = {
                "eliminated": bool(ctx.get("eliminated", False)),
                "eliminated_round": ctx.get("eliminated_round"),
                "rounds_survived": ctx.get("rounds_survived", total_questions),
            }
        elif active_mode == "quiz_royale":
            res["royale_details"] = {
                "lives_remaining": ctx.get("lives_remaining", 0),
                "rounds_survived": ctx.get("rounds_survived", total_questions),
            }
        elif active_mode == "power_play":
            res["power_play_details"] = {
                "abilities_used": ctx.get("power_play_abilities", {}),
            }

        return res

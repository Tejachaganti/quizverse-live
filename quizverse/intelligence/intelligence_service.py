"""Central AI intelligence orchestration service connecting question bank, player history, and personalized learning."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import selectinload

from quizverse.ai_generation import GenerationService
from quizverse.intelligence.adaptive_difficulty import AdaptiveDifficultyEngine, AdaptiveDifficultyResult
from quizverse.intelligence.answer_validation import AnswerValidator, AnswerValidationResult
from quizverse.intelligence.duplicate_detection import DuplicateCandidate, DuplicateCheckResult, DuplicateDetector
from quizverse.intelligence.learning_paths import GeneratedLearningPath, LearningPathGenerator
from quizverse.intelligence.learning_reports import LearningReportGenerator, LearningReportResult
from quizverse.intelligence.performance import PerformanceIntelligenceAnalyzer
from quizverse.intelligence.question_quality import QuestionQualityAnalyzer, QuestionQualityResult
from quizverse.intelligence.recommendations import QuizRecommendation, QuizRecommender
from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    Category,
    CompetitiveMatch,
    LearningPathStep,
    MatchParticipant,
    PlayerLearningPath,
    PlayerLearningReport,
    PlayerProfile,
    PlayerTopicPerformance,
    QuestionQualityAssessment,
    Quiz,
    QuizQuestion,
    QuizRecommendationRecord,
)
from quizverse.questions import QUESTIONS


class AIIntelligenceService:
    """Master orchestration service for question quality, answer validation, duplicate detection, and adaptive learning."""

    def __init__(self, database: Database, ai_service: GenerationService | None = None) -> None:
        self._database = database
        self._ai_service = ai_service

        self.quality_analyzer = QuestionQualityAnalyzer()
        self.answer_validator = AnswerValidator()
        self.duplicate_detector = DuplicateDetector()
        self.adaptive_engine = AdaptiveDifficultyEngine()
        from quizverse.intelligence.weakness_detection import WeaknessDetector
        self.weakness_detector = WeaknessDetector()
        self.learning_path_generator = LearningPathGenerator()
        self.quiz_recommender = QuizRecommender()
        self.performance_analyzer = PerformanceIntelligenceAnalyzer()
        self.report_generator = LearningReportGenerator(ai_service=ai_service)

    # =========================================================================
    # 1. QUESTION QUALITY & VALIDATION
    # =========================================================================

    def analyze_question(
        self,
        prompt: str,
        options: list[str] | tuple[str, ...],
        correct_option_index: int,
        explanation: str = "",
        category: str = "",
        difficulty: str = "medium",
        quiz_id: str | None = None,
        question_id: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Runs quality analysis, answer validation, and duplicate checking."""
        clean_prompt = (prompt or "").strip()
        prompt_hash = hashlib.sha256(clean_prompt.encode("utf-8")).hexdigest()

        # 1. Quality dimensions
        quality = self.quality_analyzer.analyze(
            prompt=clean_prompt,
            options=options,
            correct_option_index=correct_option_index,
            explanation=explanation,
            category=category,
            difficulty=difficulty,
        )

        # 2. Answer and mathematical consistency
        validation = self.answer_validator.validate(
            prompt=clean_prompt,
            options=options,
            correct_option_index=correct_option_index,
            explanation=explanation,
        )

        # 3. Duplicate detection against existing questions
        candidates = self._get_duplicate_candidates(category=category)
        duplicate = self.duplicate_detector.check_duplicate(
            prompt=clean_prompt,
            candidates=candidates,
            exclude_id=question_id,
        )

        # Unified status
        final_validation_status = quality.validation_status
        if not validation.is_valid:
            final_validation_status = "rejected"
        elif validation.review_status == "needs_review" and final_validation_status == "validated":
            final_validation_status = "needs_review"

        final_review_required = quality.review_required or (not validation.is_valid) or duplicate.review_required

        # Compile combined issues and improvements
        all_issues = list(quality.detected_issues)
        for finding in validation.findings:
            if finding["status"] in ("fail", "warning"):
                all_issues.append({"type": finding["rule_name"], "severity": "error" if finding["status"] == "fail" else "warning", "message": finding["details"]})

        if duplicate.duplicate_status != "unique":
            all_issues.append({
                "type": f"duplicate_{duplicate.duplicate_status}",
                "severity": "error" if duplicate.duplicate_status in ("exact_duplicate", "normalized_duplicate") else "warning",
                "message": duplicate.duplicate_reason,
            })

        all_improvements = list(quality.suggested_improvements)
        if validation.suggested_correction:
            all_improvements.append(validation.suggested_correction)

        assessment_dict = {
            "prompt_hash": prompt_hash,
            "quality_score": quality.quality_score,
            "clarity_score": quality.clarity_score,
            "ambiguity_score": quality.ambiguity_score,
            "answer_consistency_score": quality.answer_consistency_score,
            "consistency_score": quality.answer_consistency_score,
            "explanation_score": quality.explanation_score,
            "duplicate_risk": duplicate.duplicate_status,
            "duplicate_similarity": duplicate.similarity_score,
            "matched_question_id": duplicate.matched_question_id,
            "difficulty_confidence": quality.difficulty_confidence,
            "topic_relevance": quality.topic_relevance,
            "validation_status": final_validation_status,
            "review_required": final_review_required,
            "detected_issues": all_issues,
            "suggested_improvements": all_improvements,
            "assessed_by": "deterministic",
        }

        if persist:
            assessment_id = secrets.token_hex(16)
            now = datetime.now(UTC)
            with self._database.sessions.begin() as session:
                record = QuestionQualityAssessment(
                    id=assessment_id,
                    question_id=question_id,
                    quiz_id=quiz_id,
                    prompt_hash=prompt_hash,
                    quality_score=quality.quality_score,
                    clarity_score=quality.clarity_score,
                    ambiguity_score=quality.ambiguity_score,
                    answer_consistency_score=quality.answer_consistency_score,
                    explanation_score=quality.explanation_score,
                    duplicate_risk=duplicate.duplicate_status,
                    difficulty_confidence=quality.difficulty_confidence,
                    topic_relevance=quality.topic_relevance,
                    validation_status=final_validation_status,
                    review_required=final_review_required,
                    issues_json=json.dumps(all_issues, ensure_ascii=False),
                    improvements_json=json.dumps(all_improvements, ensure_ascii=False),
                    assessed_by="deterministic",
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                assessment_dict["id"] = assessment_id

        return assessment_dict

    def validate_answer(
        self,
        prompt: str,
        options: list[str] | tuple[str, ...],
        correct_option_index: int,
        explanation: str = "",
        format_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Direct answer validation helper."""
        res = self.answer_validator.validate(
            prompt=prompt,
            options=options,
            correct_option_index=correct_option_index,
            explanation=explanation,
            format_payload=format_payload,
        )
        return res.to_dict()

    def check_duplicates(
        self,
        prompt: str,
        exclude_id: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Direct duplicate check helper."""
        candidates = self._get_duplicate_candidates(category=category)
        res = self.duplicate_detector.check_duplicate(prompt, candidates, exclude_id=exclude_id)
        return res.to_dict()

    def _get_duplicate_candidates(self, category: str | None = None) -> list[DuplicateCandidate]:
        candidates: list[DuplicateCandidate] = []

        # 1. Built-in reviewed questions
        for q in QUESTIONS:
            candidates.append(DuplicateCandidate(id=q.id, prompt=q.prompt, category=q.category, difficulty=q.difficulty))

        # 2. Database quiz questions
        with self._database.session() as session:
            query = select(QuizQuestion.id, QuizQuestion.prompt, QuizQuestion.category, QuizQuestion.difficulty).limit(200)
            if category:
                query = query.where(QuizQuestion.category.ilike(f"%{category}%"))
            rows = session.execute(query).all()
            for r in rows:
                candidates.append(DuplicateCandidate(id=r.id, prompt=r.prompt, category=r.category, difficulty=r.difficulty))

        return candidates

    # =========================================================================
    # 2. PLAYER PERFORMANCE & WEAKNESS INTELLIGENCE
    # =========================================================================

    def _get_player_attempts(self, player_id: str) -> list[dict[str, Any]]:
        with self._database.session() as session:
            query = (
                select(
                    CompetitiveMatch.id,
                    CompetitiveMatch.mode,
                    CompetitiveMatch.completed_at,
                    CompetitiveMatch.result_context,
                    MatchParticipant.final_score,
                    MatchParticipant.correct_count,
                    MatchParticipant.answer_count,
                    MatchParticipant.placement,
                    MatchParticipant.outcome,
                )
                .select_from(MatchParticipant)
                .join(CompetitiveMatch, CompetitiveMatch.id == MatchParticipant.match_id)
                .where(
                    MatchParticipant.player_id == player_id,
                    CompetitiveMatch.status == "completed",
                )
                .order_by(CompetitiveMatch.completed_at.desc(), CompetitiveMatch.id.desc())
            )
            rows = session.execute(query).all()

            attempts: list[dict[str, Any]] = []
            for r in rows:
                ctx = r.result_context or {}
                if isinstance(ctx, str):
                    try:
                        ctx = json.loads(ctx)
                    except Exception:
                        ctx = {}
                selection = ctx.get("selection") or {}
                cats = selection.get("categories") or []
                category_name = cats[0] if cats else "General Knowledge"
                diff = selection.get("difficulty") or "medium"

                attempts.append({
                    "match_id": r.id,
                    "mode": r.mode,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                    "final_score": r.final_score,
                    "correct_count": r.correct_count or 0,
                    "answer_count": r.answer_count or 0,
                    "placement": r.placement,
                    "outcome": r.outcome,
                    "category": category_name,
                    "difficulty": diff,
                    "response_time_ms": ctx.get("response_time_ms", 0),
                })
            return attempts

    def get_player_performance(self, player_id: str, time_window: str = "recent") -> dict[str, Any]:
        """Computes comprehensive performance statistics for the given time window."""
        attempts = self._get_player_attempts(player_id)
        return self.performance_analyzer.analyze_performance(attempts, window=time_window)

    def get_player_weaknesses(self, player_id: str) -> dict[str, Any]:
        """Calculates topic strengths, weaknesses, and progress trends."""
        attempts = self._get_player_attempts(player_id)

        # Group attempts by category
        topic_attempts: dict[str, list[dict[str, Any]]] = {}
        for a in attempts:
            cat = a.get("category", "General Knowledge")
            topic_attempts.setdefault(cat, []).append(a)

        # Also load persisted topic performance rows if present
        with self._database.session() as session:
            persisted = session.scalars(
                select(PlayerTopicPerformance).where(PlayerTopicPerformance.player_id == player_id)
            ).all()
            for p in persisted:
                if p.category_name not in topic_attempts:
                    topic_attempts[p.category_name] = [{
                        "correct_count": p.correct_count,
                        "answer_count": p.attempts,
                        "response_time_ms": p.avg_response_time_ms,
                    }]

        category_names = {}
        for k in topic_attempts:
            clean = k.replace("category:", "").replace("-", " ").title()
            category_names[k] = clean

        summary = self.weakness_detector.analyze_topics(topic_attempts, category_names=category_names)
        return summary.to_dict()

    def record_topic_performance(
        self,
        player_id: str,
        category_id: str,
        category_name: str,
        attempts: int,
        correct_count: int,
        difficulty: str = "medium",
        avg_response_time_ms: int = 0,
    ) -> dict[str, Any]:
        """Explicitly records or updates player topic performance."""
        now = datetime.now(UTC)
        acc = (correct_count / attempts * 100.0) if attempts > 0 else 0.0

        if attempts < 3:
            status = "insufficient_data"
            confidence = round(attempts / 3.0 * 0.5, 2)
        elif acc >= 75.0:
            status = "strong"
            confidence = min(1.0, 0.6 + (attempts / 15.0))
        elif acc < 50.0:
            status = "weak"
            confidence = min(1.0, 0.6 + (attempts / 15.0))
        else:
            status = "needs_practice"
            confidence = min(1.0, 0.5 + (attempts / 20.0))

        with self._database.sessions.begin() as session:
            existing = session.scalar(
                select(PlayerTopicPerformance).where(
                    PlayerTopicPerformance.player_id == player_id,
                    PlayerTopicPerformance.category_id == category_id,
                    PlayerTopicPerformance.difficulty == difficulty,
                )
            )
            if existing:
                existing.attempts += attempts
                existing.correct_count += correct_count
                total_att = existing.attempts
                existing.accuracy_pct = (existing.correct_count / total_att * 100.0) if total_att > 0 else 0.0
                existing.status = status
                existing.confidence = confidence
                existing.avg_response_time_ms = avg_response_time_ms or existing.avg_response_time_ms
                existing.last_attempted_at = now
                existing.updated_at = now
                record_id = existing.id
            else:
                record_id = secrets.token_hex(16)
                perf = PlayerTopicPerformance(
                    id=record_id,
                    player_id=player_id,
                    category_id=category_id,
                    category_name=category_name,
                    attempts=attempts,
                    correct_count=correct_count,
                    accuracy_pct=acc,
                    avg_response_time_ms=avg_response_time_ms,
                    difficulty=difficulty,
                    status=status,
                    trend="stable",
                    confidence=confidence,
                    last_attempted_at=now,
                    updated_at=now,
                )
                session.add(perf)

        return {
            "id": record_id,
            "player_id": player_id,
            "category_id": category_id,
            "category_name": category_name,
            "accuracy_pct": round(acc, 1),
            "status": status,
            "confidence": confidence,
        }

    # =========================================================================
    # 3. ADAPTIVE DIFFICULTY
    # =========================================================================

    def get_adaptive_difficulty(self, player_id: str, current_preference: str = "medium") -> dict[str, Any]:
        """Calculates explainable difficulty recommendation based on validated play evidence."""
        attempts = self._get_player_attempts(player_id)
        res = self.adaptive_engine.evaluate(current_difficulty=current_preference, attempts=attempts)
        return res.to_dict()

    # =========================================================================
    # 4. PERSONALIZED LEARNING PATHS
    # =========================================================================

    def get_or_create_learning_path(
        self,
        player_id: str,
        regenerate: bool = False,
    ) -> dict[str, Any]:
        """Retrieves active learning path or generates a new personalized path."""
        with self._database.session() as session:
            existing = session.scalar(
                select(PlayerLearningPath)
                .options(selectinload(PlayerLearningPath.steps))
                .where(
                    PlayerLearningPath.player_id == player_id,
                    PlayerLearningPath.status == "active",
                )
                .order_by(desc(PlayerLearningPath.created_at))
            )
            if existing and not regenerate:
                return self._path_view(existing)

        # Generate a new learning path
        weakness = self.get_player_weaknesses(player_id)
        diff_info = self.get_adaptive_difficulty(player_id)

        # Available quizzes for reference
        with self._database.session() as session:
            quizzes = session.execute(
                select(Quiz.id, Quiz.title, Quiz.category, Quiz.difficulty)
                .where(Quiz.status == "published", Quiz.deleted_at.is_(None))
                .limit(20)
            ).all()
            quiz_list = [{"id": q.id, "title": q.title, "category": q.category, "difficulty": q.difficulty} for q in quizzes]

        path_spec = self.learning_path_generator.generate_path(
            weak_topics=weakness.get("weak_topics", []),
            strong_topics=weakness.get("strong_topics", []),
            preferences={},
            available_quizzes=quiz_list,
            recommended_difficulty=diff_info.get("recommended_difficulty", "medium"),
        )

        now = datetime.now(UTC)
        path_id = secrets.token_hex(16)

        with self._database.sessions.begin() as session:
            if regenerate:
                # Mark previous active paths as archived
                old_paths = session.scalars(
                    select(PlayerLearningPath).where(
                        PlayerLearningPath.player_id == player_id,
                        PlayerLearningPath.status == "active",
                    )
                ).all()
                for op in old_paths:
                    op.status = "archived"
                    op.updated_at = now

            path_model = PlayerLearningPath(
                id=path_id,
                player_id=player_id,
                title=path_spec.title,
                objective=path_spec.objective,
                target_category=path_spec.target_category,
                target_subcategory=path_spec.target_subcategory,
                recommended_difficulty=path_spec.recommended_difficulty,
                status="active",
                progress_pct=0.0,
                total_steps=len(path_spec.steps),
                completed_steps=0,
                estimated_minutes=path_spec.estimated_minutes,
                created_at=now,
                updated_at=now,
            )
            session.add(path_model)

            for step in path_spec.steps:
                step_id = secrets.token_hex(16)
                s_model = LearningPathStep(
                    id=step_id,
                    path_id=path_id,
                    step_number=step.step_number,
                    title=step.title,
                    description=step.description,
                    activity_type=step.activity_type,
                    target_quiz_id=step.target_quiz_id,
                    target_category=step.target_category,
                    target_difficulty=step.target_difficulty,
                    estimated_minutes=step.estimated_minutes,
                    is_completed=False,
                    completed_at=None,
                    created_at=now,
                )
                session.add(s_model)

        with self._database.session() as session:
            created = session.scalar(
                select(PlayerLearningPath)
                .options(selectinload(PlayerLearningPath.steps))
                .where(PlayerLearningPath.id == path_id)
            )
            assert created is not None
            return self._path_view(created)

    def complete_learning_step(self, player_id: str, path_id: str, step_id: str) -> dict[str, Any]:
        """Marks a learning path step complete and advances progress."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            path = session.scalar(
                select(PlayerLearningPath)
                .options(selectinload(PlayerLearningPath.steps))
                .where(PlayerLearningPath.id == path_id, PlayerLearningPath.player_id == player_id)
            )
            if not path:
                raise ValueError("Learning path not found or unauthorized.")

            step = next((s for s in path.steps if s.id == step_id or str(s.step_number) == step_id), None)
            if not step:
                raise ValueError("Learning path step not found.")

            if not step.is_completed:
                step.is_completed = True
                step.completed_at = now
                path.completed_steps = sum(1 for s in path.steps if s.is_completed)
                path.progress_pct = round((path.completed_steps / max(1, path.total_steps)) * 100.0, 1)
                if path.completed_steps >= path.total_steps:
                    path.status = "completed"
                path.updated_at = now

        with self._database.session() as session:
            updated = session.scalar(
                select(PlayerLearningPath)
                .options(selectinload(PlayerLearningPath.steps))
                .where(PlayerLearningPath.id == path_id)
            )
            assert updated is not None
            return self._path_view(updated)

    def _path_view(self, path: PlayerLearningPath) -> dict[str, Any]:
        return {
            "id": path.id,
            "player_id": path.player_id,
            "title": path.title,
            "objective": path.objective,
            "topic": path.target_category,
            "target_category": path.target_category,
            "target_subcategory": path.target_subcategory,
            "recommended_difficulty": path.recommended_difficulty,
            "target_difficulty": path.recommended_difficulty,
            "status": path.status,
            "progress_pct": path.progress_pct,
            "progress_percent": path.progress_pct,
            "total_steps": path.total_steps,
            "completed_steps": path.completed_steps,
            "estimated_minutes": path.estimated_minutes,
            "created_at": path.created_at.isoformat() if path.created_at else None,
            "updated_at": path.updated_at.isoformat() if path.updated_at else None,
            "steps": [
                {
                    "id": s.id,
                    "step_number": s.step_number,
                    "title": s.title,
                    "description": s.description,
                    "activity_type": s.activity_type,
                    "step_type": s.activity_type,
                    "target_quiz_id": s.target_quiz_id,
                    "target_category": s.target_category,
                    "target_difficulty": s.target_difficulty,
                    "estimated_minutes": s.estimated_minutes,
                    "is_completed": s.is_completed,
                    "status": "completed" if s.is_completed else "pending",
                    "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                }
                for s in sorted(path.steps, key=lambda x: x.step_number)
            ],
        }

    # =========================================================================
    # 5. INTELLIGENT QUIZ RECOMMENDATIONS
    # =========================================================================

    def get_recommendations(self, player_id: str, limit: int = 6) -> list[dict[str, Any]]:
        """Generates explainable quiz recommendations across distinct learning strategies."""
        weakness = self.get_player_weaknesses(player_id)
        diff_info = self.get_adaptive_difficulty(player_id)

        with self._database.session() as session:
            quizzes = session.execute(
                select(Quiz.id, Quiz.title, Quiz.category, Quiz.difficulty)
                .where(Quiz.status == "published", Quiz.deleted_at.is_(None))
                .limit(50)
            ).all()
            quiz_list = [
                {"id": q.id, "title": q.title, "category": q.category, "difficulty": q.difficulty}
                for q in quizzes
            ]

        recommendations = self.quiz_recommender.recommend(
            weak_topics=weakness.get("weak_topics", []),
            strong_topics=weakness.get("strong_topics", []),
            available_quizzes=quiz_list,
            excluded_categories=set(),
            recommended_difficulty=diff_info.get("recommended_difficulty", "medium"),
            limit=limit,
        )

        # Persist top recommendations
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            for rec in recommendations:
                record = QuizRecommendationRecord(
                    id=secrets.token_hex(16),
                    player_id=player_id,
                    quiz_id=rec.quiz_id,
                    quiz_title=rec.quiz_title,
                    category=rec.category,
                    difficulty=rec.difficulty,
                    reason_type=rec.reason_type,
                    reason_text=rec.reason_text,
                    score=rec.score,
                    is_dismissed=False,
                    created_at=now,
                )
                session.add(record)

        return [r.to_dict() for r in recommendations]

    # =========================================================================
    # 6. AI LEARNING REPORTS
    # =========================================================================

    async def generate_learning_report(
        self,
        player_id: str,
        player_name: str,
        time_window: str = "recent",
        report_type: str = "comprehensive",
    ) -> dict[str, Any]:
        """Generates and persists a structured learning diagnostic report."""
        perf = self.get_player_performance(player_id, time_window=time_window)
        weakness = self.get_player_weaknesses(player_id)
        adaptive = self.get_adaptive_difficulty(player_id)

        result = await self.report_generator.generate_report(
            player_name=player_name,
            performance_stats=perf,
            weakness_summary=weakness,
            adaptive_difficulty=adaptive,
            time_window=time_window,
            report_type=report_type,
        )

        now = datetime.now(UTC)
        report_id = secrets.token_hex(16)

        with self._database.sessions.begin() as session:
            report_row = PlayerLearningReport(
                id=report_id,
                player_id=player_id,
                report_type=result.report_type,
                time_window=result.time_window,
                summary=result.summary,
                data_json=json.dumps(result.data, ensure_ascii=False),
                generated_by=result.generated_by,
                created_at=now,
            )
            session.add(report_row)

        report_dict = result.to_dict()
        report_dict["id"] = report_id
        report_dict["created_at"] = now.isoformat()
        return report_dict

    def list_learning_reports(self, player_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Lists historical learning reports for an authenticated player."""
        with self._database.session() as session:
            rows = session.scalars(
                select(PlayerLearningReport)
                .where(PlayerLearningReport.player_id == player_id)
                .order_by(desc(PlayerLearningReport.created_at))
                .limit(limit)
            ).all()

            return [
                {
                    "id": r.id,
                    "report_type": r.report_type,
                    "time_window": r.time_window,
                    "summary": r.summary,
                    "data": json.loads(r.data_json) if r.data_json else {},
                    "generated_by": r.generated_by,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]

    def get_learning_report(self, player_id: str, report_id: str) -> dict[str, Any] | None:
        """Retrieves a single learning report by ID."""
        with self._database.session() as session:
            r = session.scalar(
                select(PlayerLearningReport).where(
                    PlayerLearningReport.id == report_id,
                    PlayerLearningReport.player_id == player_id,
                )
            )
            if not r:
                return None
            return {
                "id": r.id,
                "report_type": r.report_type,
                "time_window": r.time_window,
                "summary": r.summary,
                "data": json.loads(r.data_json) if r.data_json else {},
                "generated_by": r.generated_by,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }

    # =========================================================================
    # 7. OVERVIEW INTELLIGENCE DASHBOARD
    # =========================================================================

    def get_intelligence_overview(self, player_id: str) -> dict[str, Any]:
        """Compiles a complete snapshot for the AI Intelligence Hub header and main tab."""
        perf = self.get_player_performance(player_id, time_window="recent")
        weakness = self.get_player_weaknesses(player_id)
        adaptive = self.get_adaptive_difficulty(player_id)
        recommendations = self.get_recommendations(player_id, limit=3)

        # Active learning path
        active_path = None
        with self._database.session() as session:
            path = session.scalar(
                select(PlayerLearningPath)
                .options(selectinload(PlayerLearningPath.steps))
                .where(PlayerLearningPath.player_id == player_id, PlayerLearningPath.status == "active")
                .order_by(desc(PlayerLearningPath.created_at))
            )
            if path:
                active_path = self._path_view(path)

        acc = perf.get("overall_accuracy_pct", 0.0)
        mastery = "Master" if acc >= 80.0 else "Practitioner" if acc >= 60.0 else "Apprentice"

        return {
            "summary": {
                "overall_accuracy": acc,
                "overall_accuracy_pct": acc,
                "total_questions_answered": perf.get("total_answered", 0),
                "total_topics_tracked": weakness.get("total_topics_analyzed", 0),
                "mastery_level": mastery,
            },
            "performance_summary": {
                "overall_accuracy_pct": perf.get("overall_accuracy_pct", 0.0),
                "total_attempts": perf.get("total_attempts", 0),
                "avg_response_time_ms": perf.get("avg_response_time_ms", 0),
                "current_streak": perf.get("current_streak", {"type": "none", "count": 0}),
            },
            "adaptive_difficulty": adaptive,
            "top_weak_topics": weakness.get("weak_topics", [])[:2],
            "top_strong_topics": weakness.get("strong_topics", [])[:2],
            "active_learning_path": active_path,
            "top_recommendations": recommendations,
        }

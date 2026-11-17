"""Quiz publication lifecycle, AI validation, visibility enforcement, and play logging."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from quizverse.intelligence.duplicate_detection import DuplicateDetector
from quizverse.intelligence.question_quality import QuestionQualityAnalyzer
from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    AnonymousOwner,
    CreatorFollow,
    CreatorProfile,
    PlayerProfile,
    Quiz,
    QuizPlay,
    QuizPublication,
    QuizQuestion,
)


class PublicationService:
    """Enforces server-authoritative quiz publication lifecycle and validation."""

    def __init__(
        self,
        database: Database,
        quality_analyzer: QuestionQualityAnalyzer | None = None,
        duplicate_detector: DuplicateDetector | None = None,
        notification_service: Any = None,
    ) -> None:
        self._database = database
        self._quality_analyzer = quality_analyzer or QuestionQualityAnalyzer()
        self._duplicate_detector = duplicate_detector or DuplicateDetector()
        self._notification_service = notification_service

    def create_or_claim_publication(
        self,
        creator_id: str,
        quiz_id: str,
        *,
        visibility: str = "public",
        status: str = "draft",
    ) -> dict[str, Any]:
        """Creates or links a publication metadata record for a quiz owned by creator."""
        if visibility not in {"public", "private", "unlisted"}:
            raise ValueError("Visibility must be 'public', 'private', or 'unlisted'.")
        if status not in {"draft", "pending_review", "published", "rejected", "archived", "suspended"}:
            raise ValueError("Invalid publication status.")

        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            # Check quiz exists
            quiz = session.scalar(select(Quiz).where(Quiz.id == quiz_id))
            if not quiz:
                raise LookupError("Quiz not found.")

            # Check if publication already exists
            pub = session.scalar(select(QuizPublication).where(QuizPublication.quiz_id == quiz_id))
            if pub:
                if pub.creator_id != creator_id:
                    raise PermissionError("You are not the creator of this quiz publication.")
                pub.visibility = visibility
                pub.status = status
                pub.updated_at = now
                session.add(pub)
                session.flush()
                return self._serialize_publication(pub, quiz)

            # Ensure creator profile exists
            creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == creator_id))
            if not creator:
                player = session.scalar(select(PlayerProfile).where(PlayerProfile.id == creator_id))
                creator_name = player.display_name if player else "Creator"
                creator = CreatorProfile(
                    id=creator_id,
                    creator_display_name=creator_name,
                    creator_specializations=[],
                    creator_visibility="public",
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(creator)
                session.flush()

            slug = secrets.token_urlsafe(8)
            pub = QuizPublication(
                id=secrets.token_hex(16),
                quiz_id=quiz_id,
                creator_id=creator_id,
                visibility=visibility,
                status=status,
                share_slug=slug,
                version=1,
                published_at=now if status == "published" else None,
                total_plays=0,
                total_reviews=0,
                rating_sum=0,
                average_rating=0.0,
                quality_score=None,
                validation_summary=None,
                created_at=now,
                updated_at=now,
            )
            session.add(pub)
            session.flush()
            return self._serialize_publication(pub, quiz)

    def publish_quiz(
        self,
        creator_id: str,
        quiz_id: str,
        *,
        visibility: str = "public",
    ) -> dict[str, Any]:
        """Validates question quality and duplicate status before public publication."""
        if visibility not in {"public", "unlisted", "private"}:
            raise ValueError("Visibility must be 'public', 'unlisted', or 'private'.")

        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            quiz = session.scalar(
                select(Quiz)
                .options(selectinload(Quiz.questions))
                .where(Quiz.id == quiz_id)
            )
            if not quiz:
                raise LookupError("Quiz not found.")

            pub = session.scalar(select(QuizPublication).where(QuizPublication.quiz_id == quiz_id))
            if pub and pub.creator_id != creator_id:
                raise PermissionError("You are not authorized to publish this quiz.")

            # Validate question count and integrity
            questions = sorted(quiz.questions, key=lambda q: q.position)
            if len(questions) < 2:
                raise ValueError("Quizzes require at least 2 questions to be published.")

            quality_scores: list[float] = []
            findings: list[dict[str, Any]] = []

            for q in questions:
                opts = [q.option_a, q.option_b, q.option_c, q.option_d]
                clean_opts = [o for o in opts if o is not None]
                if len(clean_opts) < 2:
                    raise ValueError(f"Question '{q.prompt[:30]}...' must have at least 2 options.")
                if q.correct_option_index is None or q.correct_option_index < 0 or q.correct_option_index >= len(clean_opts):
                    raise ValueError(f"Question '{q.prompt[:30]}...' has an invalid correct option index.")

                analysis = self._quality_analyzer.analyze(
                    prompt=q.prompt,
                    options=clean_opts,
                    correct_option_index=q.correct_option_index,
                    explanation=q.explanation or "",
                    category=quiz.category,
                    difficulty=quiz.difficulty,
                )
                quality_scores.append(analysis.quality_score)
                if analysis.issues:
                    findings.append({
                        "prompt": q.prompt[:40],
                        "score": analysis.quality_score,
                        "issues": [i.description for i in analysis.issues],
                    })

            avg_quality = round(sum(quality_scores) / max(1, len(quality_scores)), 1)

            # Check duplicate prompt risk against other quizzes
            existing_questions = session.scalars(
                select(QuizQuestion.prompt)
                .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
                .where(Quiz.id != quiz_id, Quiz.status == "published")
                .limit(200)
            ).all()

            candidate_dicts = [{"id": p, "prompt": p} for p in existing_questions]
            duplicate_warnings = []
            if candidate_dicts:
                for q in questions:
                    dup_res = self._duplicate_detector.check_duplicate(q.prompt, candidate_dicts)
                    if dup_res.duplicate_status != "unique":
                        duplicate_warnings.append({
                            "prompt": q.prompt[:40],
                            "match": str(dup_res.matched_question_id)[:40],
                            "similarity": dup_res.similarity_score,
                        })

            validation_summary = {
                "question_count": len(questions),
                "average_quality_score": avg_quality,
                "findings": findings[:10],
                "duplicate_warnings": duplicate_warnings[:5],
                "validated_at": now.isoformat(),
            }

            # Ensure creator profile exists
            creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == creator_id))
            if not creator:
                player = session.scalar(select(PlayerProfile).where(PlayerProfile.id == creator_id))
                creator_name = player.display_name if player else "Creator"
                creator = CreatorProfile(
                    id=creator_id,
                    creator_display_name=creator_name,
                    creator_specializations=[],
                    creator_visibility="public",
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(creator)
                session.flush()

            if pub is None:
                slug = secrets.token_urlsafe(8)
                pub = QuizPublication(
                    id=secrets.token_hex(16),
                    quiz_id=quiz_id,
                    creator_id=creator_id,
                    visibility=visibility,
                    status="published",
                    share_slug=slug,
                    version=1,
                    published_at=now,
                    total_plays=0,
                    total_reviews=0,
                    rating_sum=0,
                    average_rating=0.0,
                    quality_score=avg_quality,
                    validation_summary=validation_summary,
                    created_at=now,
                    updated_at=now,
                )
                session.add(pub)
            else:
                pub.visibility = visibility
                pub.status = "published"
                pub.published_at = now
                pub.quality_score = avg_quality
                pub.validation_summary = validation_summary
                pub.updated_at = now
                session.add(pub)

            # Update creator's published quiz count
            session.flush()
            creator.published_quiz_count = session.scalar(
                select(func.count(QuizPublication.id)).where(
                    QuizPublication.creator_id == creator_id,
                    QuizPublication.status == "published",
                )
            ) or 0
            session.add(creator)

            # Update underlying Quiz row
            quiz.status = "published"
            quiz.visibility = "public" if visibility == "public" else "private"
            quiz.published_at = now
            session.add(quiz)

            # Update creator statistics
            creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == creator_id))
            if creator:
                creator.published_quiz_count = session.scalar(
                    select(func.count(QuizPublication.id)).where(
                        QuizPublication.creator_id == creator_id,
                        QuizPublication.status == "published",
                    )
                ) + 1
                session.add(creator)

            # Fetch followers for notification dispatch
            followers = session.scalars(
                select(CreatorFollow.follower_id).where(CreatorFollow.creator_id == creator_id)
            ).all()

            creator_name = creator.creator_display_name if creator else "A creator"
            quiz_title = quiz.title

        # Dispatch notifications to followers if public
        if visibility == "public" and self._notification_service is not None:
            for fid in followers:
                self._notification_service.create_notification(
                    recipient_id=fid,
                    actor_id=creator_id,
                    notification_type="creator_quiz_published",
                    title="New Quiz Published!",
                    body=f"{creator_name} published a new quiz: '{quiz_title}'. Test your skills now!",
                    target_type="quiz",
                    target_id=quiz_id,
                    deduplication_key=f"pub:{quiz_id}:{fid}",
                )

        return self._serialize_publication(pub, quiz)

    def unpublish_quiz(self, creator_id: str, quiz_id: str) -> dict[str, Any]:
        """Moves published quiz back to draft state."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            pub = session.scalar(select(QuizPublication).where(QuizPublication.quiz_id == quiz_id))
            if not pub or pub.creator_id != creator_id:
                raise PermissionError("You are not authorized to unpublish this quiz.")

            pub.status = "draft"
            pub.updated_at = now
            session.add(pub)

            quiz = session.scalar(select(Quiz).where(Quiz.id == quiz_id))
            if quiz:
                quiz.status = "draft"
                quiz.visibility = "private"
                session.add(quiz)

            creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == creator_id))
            if creator:
                creator.published_quiz_count = max(
                    0,
                    session.scalar(
                        select(func.count(QuizPublication.id)).where(
                            QuizPublication.creator_id == creator_id,
                            QuizPublication.status == "published",
                        )
                    ) - 1,
                )
                session.add(creator)

            return self._serialize_publication(pub, quiz)

    def submit_for_review(self, creator_id: str, quiz_id: str) -> dict[str, Any]:
        """Marks quiz as pending review before public listing."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            pub = session.scalar(select(QuizPublication).where(QuizPublication.quiz_id == quiz_id))
            if not pub or pub.creator_id != creator_id:
                raise PermissionError("You are not authorized to submit this quiz for review.")

            pub.status = "pending_review"
            pub.updated_at = now
            session.add(pub)

            quiz = session.scalar(select(Quiz).where(Quiz.id == quiz_id))
            return self._serialize_publication(pub, quiz)

    def archive_quiz(self, creator_id: str, quiz_id: str) -> dict[str, Any]:
        """Archives a quiz so it is no longer listed publicly."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            pub = session.scalar(select(QuizPublication).where(QuizPublication.quiz_id == quiz_id))
            if not pub or pub.creator_id != creator_id:
                raise PermissionError("You are not authorized to archive this quiz.")

            pub.status = "archived"
            pub.visibility = "private"
            pub.updated_at = now
            session.add(pub)

            quiz = session.scalar(select(Quiz).where(Quiz.id == quiz_id))
            if quiz:
                quiz.visibility = "private"
                session.add(quiz)

            return self._serialize_publication(pub, quiz)

    def record_quiz_play(
        self,
        player_id: str,
        quiz_id: str,
        score: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Logs a play session for rating eligibility and creator statistics."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            play = QuizPlay(
                id=secrets.token_hex(16),
                quiz_id=quiz_id,
                player_id=player_id,
                score=score,
                completed_at=now,
            )
            session.add(play)
            session.flush()

            # Update play count on publication and creator profile
            pub = session.scalar(select(QuizPublication).where(QuizPublication.quiz_id == quiz_id))
            if pub:
                pub.total_plays = session.scalar(
                    select(func.count(QuizPlay.id)).where(QuizPlay.quiz_id == quiz_id)
                ) or 0
                session.add(pub)

                creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == pub.creator_id))
                if creator:
                    creator.total_plays = session.scalar(
                        select(func.count(QuizPlay.id))
                        .join(QuizPublication, QuizPublication.quiz_id == QuizPlay.quiz_id)
                        .where(QuizPublication.creator_id == pub.creator_id)
                    ) or 0
                    session.add(creator)

            return {"status": "play_recorded", "quiz_id": quiz_id, "player_id": player_id, "score": score, "recorded_at": now.isoformat()}

    def get_publication(
        self,
        quiz_id: str,
        viewer_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetches publication record enforcing visibility and status permissions."""
        with self._database.session() as session:
            pub = session.scalar(
                select(QuizPublication)
                .options(selectinload(QuizPublication.quiz))
                .where(QuizPublication.quiz_id == quiz_id)
            )
            if not pub:
                raise LookupError("Quiz publication not found.")

            quiz = pub.quiz
            if pub.status in {"suspended", "rejected"} and pub.creator_id != viewer_id:
                raise PermissionError("This quiz is currently suspended or under moderation review.")

            if pub.visibility == "private" and pub.creator_id != viewer_id:
                raise PermissionError("This quiz is private to its creator.")

            return self._serialize_publication(pub, quiz)

    def get_publication_by_slug(self, slug: str, viewer_id: str | None = None) -> dict[str, Any]:
        """Resolves shareable unlisted or public slug link."""
        with self._database.session() as session:
            pub = session.scalar(
                select(QuizPublication)
                .options(selectinload(QuizPublication.quiz))
                .where(QuizPublication.share_slug == slug)
            )
            if not pub:
                raise LookupError("Shared quiz link not found or expired.")

            if pub.status in {"suspended", "rejected"} and pub.creator_id != viewer_id:
                raise PermissionError("This shared quiz is currently unavailable.")

            return self._serialize_publication(pub, pub.quiz)

    def list_creator_quizzes(
        self,
        creator_id: str,
        viewer_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lists quizzes authored by a creator, with full visibility for the creator himself."""
        is_owner = viewer_id == creator_id
        with self._database.session() as session:
            query = (
                select(QuizPublication)
                .options(selectinload(QuizPublication.quiz))
                .where(QuizPublication.creator_id == creator_id)
            )
            if not is_owner:
                query = query.where(
                    QuizPublication.status == "published",
                    QuizPublication.visibility == "public",
                )
            elif status:
                query = query.where(QuizPublication.status == status)

            query = query.order_by(QuizPublication.updated_at.desc())
            pubs = session.scalars(query).all()
            return [self._serialize_publication(p, p.quiz) for p in pubs]

    def _serialize_publication(self, p: QuizPublication, q: Quiz | None) -> dict[str, Any]:
        return {
            "id": p.id,
            "quiz_id": p.quiz_id,
            "creator_id": p.creator_id,
            "title": q.title if q else "Untitled Quiz",
            "description": q.description if q else None,
            "category": q.category if q else "General",
            "difficulty": q.difficulty if q else "medium",
            "visibility": p.visibility,
            "status": p.status,
            "rejection_reason": p.rejection_reason,
            "suspension_reason": p.suspension_reason,
            "share_slug": p.share_slug,
            "share_url": f"/community/quizzes/share/{p.share_slug}" if p.share_slug else None,
            "version": p.version,
            "published_at": p.published_at.isoformat() if p.published_at else None,
            "total_plays": p.total_plays,
            "total_reviews": p.total_reviews,
            "average_rating": round(p.average_rating, 2),
            "quality_score": p.quality_score,
            "validation_summary": p.validation_summary,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }

"""Ratings, verified player reviews, helpful votes, and creator responses."""

from __future__ import annotations

import html
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    CreatorProfile,
    PlayerBlock,
    PlayerProfile,
    Quiz,
    QuizPlay,
    QuizPublication,
    QuizReview,
    QuizReviewHelpful,
)


class ReviewService:
    """Manages verified player ratings, reviews, aggregation, and safety."""

    def __init__(self, database: Database, notification_service: Any = None, social_service: Any = None) -> None:
        self._database = database
        self._notification_service = notification_service
        self._social_service = social_service

    def submit_review(
        self,
        player_id: str,
        quiz_id: str,
        rating: int,
        review_text: str | None = None,
    ) -> dict[str, Any]:
        """Submits or updates a verified rating and review."""
        if not (1 <= rating <= 5):
            raise ValueError("Rating must be an integer between 1 and 5.")

        clean_text = review_text.strip() if review_text else None
        if clean_text and len(clean_text) > 1000:
            raise ValueError("Review text must not exceed 1000 characters.")

        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            # 1. Check publication
            pub = session.scalar(
                select(QuizPublication)
                .options(selectinload(QuizPublication.quiz))
                .where(QuizPublication.quiz_id == quiz_id)
            )
            if not pub:
                raise LookupError("Quiz publication not found.")

            if pub.creator_id == player_id:
                raise ValueError("Creators cannot rate their own quizzes.")

            # 2. Check blocking
            if self._is_blocked(player_id, pub.creator_id):
                raise PermissionError("Interaction blocked due to privacy settings.")

            # 3. Check play eligibility: player must have actually played the quiz
            has_played = session.scalar(
                select(QuizPlay.id).where(QuizPlay.quiz_id == quiz_id, QuizPlay.player_id == player_id).limit(1)
            )
            if not has_played:
                raise ValueError("You must play this quiz before you can submit a rating or review.")

            # 4. Check existing review
            review = session.scalar(
                select(QuizReview).where(QuizReview.quiz_id == quiz_id, QuizReview.player_id == player_id)
            )
            is_new = review is None
            if is_new:
                review = QuizReview(
                    id=secrets.token_hex(16),
                    quiz_id=quiz_id,
                    player_id=player_id,
                    rating=rating,
                    review_text=clean_text,
                    creator_response=None,
                    creator_responded_at=None,
                    helpful_count=0,
                    is_hidden=False,
                    created_at=now,
                    updated_at=now,
                )
                session.add(review)
            else:
                review.rating = rating
                review.review_text = clean_text
                review.updated_at = now
                session.add(review)

            session.flush()

            # 5. Recompute aggregate rating on QuizPublication
            reviews = session.scalars(
                select(QuizReview).where(QuizReview.quiz_id == quiz_id, QuizReview.is_hidden.is_(False))
            ).all()
            total_revs = len(reviews)
            rating_sum = sum(r.rating for r in reviews)
            avg_rating = round(rating_sum / max(1, total_revs), 2) if total_revs else 0.0

            pub.total_reviews = total_revs
            pub.rating_sum = rating_sum
            pub.average_rating = avg_rating
            session.add(pub)

            # 6. Recompute creator overall average rating
            creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == pub.creator_id))
            if creator:
                creator_pubs = session.scalars(
                    select(QuizPublication).where(
                        QuizPublication.creator_id == pub.creator_id,
                        QuizPublication.status == "published",
                    )
                ).all()
                c_revs = sum(p.total_reviews for p in creator_pubs)
                c_sum = sum(p.rating_sum for p in creator_pubs)
                creator.total_reviews = c_revs
                creator.average_rating = round(c_sum / max(1, c_revs), 2) if c_revs else 0.0
                session.add(creator)

            reviewer = session.scalar(select(PlayerProfile).where(PlayerProfile.id == player_id))
            reviewer_name = reviewer.display_name if reviewer else "A player"
            quiz_title = pub.quiz.title if pub.quiz else "Quiz"
            creator_id = pub.creator_id

        # Dispatch notification to creator if new review
        if is_new and self._notification_service is not None:
            self._notification_service.create_notification(
                recipient_id=creator_id,
                actor_id=player_id,
                notification_type="quiz_review",
                title=f"New {rating}★ Review!",
                body=f"{reviewer_name} rated '{quiz_title}' {rating} stars.",
                target_type="quiz",
                target_id=quiz_id,
                deduplication_key=f"rev:{quiz_id}:{player_id}",
            )

        return self._serialize_review(review, reviewer_name)

    def delete_review(self, player_id: str, review_id: str) -> dict[str, Any]:
        """Deletes own review and recalculates quiz and creator aggregate ratings."""
        with self._database.sessions.begin() as session:
            review = session.scalar(select(QuizReview).where(QuizReview.id == review_id))
            if not review or review.player_id != player_id:
                raise PermissionError("You can only delete your own review.")

            quiz_id = review.quiz_id
            session.delete(review)
            session.flush()

            # Recalculate
            pub = session.scalar(select(QuizPublication).where(QuizPublication.quiz_id == quiz_id))
            if pub:
                reviews = session.scalars(
                    select(QuizReview).where(QuizReview.quiz_id == quiz_id, QuizReview.is_hidden.is_(False))
                ).all()
                total_revs = len(reviews)
                rating_sum = sum(r.rating for r in reviews)
                avg_rating = round(rating_sum / max(1, total_revs), 2) if total_revs else 0.0

                pub.total_reviews = total_revs
                pub.rating_sum = rating_sum
                pub.average_rating = avg_rating
                session.add(pub)

                creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == pub.creator_id))
                if creator:
                    creator_pubs = session.scalars(
                        select(QuizPublication).where(
                            QuizPublication.creator_id == pub.creator_id,
                            QuizPublication.status == "published",
                        )
                    ).all()
                    c_revs = sum(p.total_reviews for p in creator_pubs)
                    c_sum = sum(p.rating_sum for p in creator_pubs)
                    creator.total_reviews = c_revs
                    creator.average_rating = round(c_sum / max(1, c_revs), 2) if c_revs else 0.0
                    session.add(creator)

            return {"status": "deleted", "review_id": review_id}

    def creator_respond(
        self,
        creator_id: str,
        review_id: str,
        response_text: str,
    ) -> dict[str, Any]:
        """Allows quiz creator to post an official response to a review."""
        clean_text = response_text.strip()
        if not clean_text or len(clean_text) > 1000:
            raise ValueError("Response text must be between 1 and 1000 characters.")

        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            review = session.scalar(
                select(QuizReview)
                .options(selectinload(QuizReview.quiz))
                .where(QuizReview.id == review_id)
            )
            if not review:
                raise LookupError("Review not found.")

            pub = session.scalar(select(QuizPublication).where(QuizPublication.quiz_id == review.quiz_id))
            if not pub or pub.creator_id != creator_id:
                raise PermissionError("Only the quiz creator can respond to this review.")

            review.creator_response = clean_text
            review.creator_responded_at = now
            session.add(review)

            reviewer_id = review.player_id
            creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == creator_id))
            creator_name = creator.creator_display_name if creator else "The creator"
            quiz_title = review.quiz.title if review.quiz else "Quiz"

        # Dispatch notification to reviewer
        if self._notification_service is not None:
            self._notification_service.create_notification(
                recipient_id=reviewer_id,
                actor_id=creator_id,
                notification_type="creator_response",
                title="Creator Responded to Your Review",
                body=f"{creator_name} responded to your review on '{quiz_title}'.",
                target_type="review",
                target_id=review_id,
                deduplication_key=f"resp:{review_id}:{now.timestamp()}",
            )

        return {
            "status": "responded",
            "review_id": review_id,
            "creator_response": clean_text,
            "creator_responded_at": now.isoformat(),
        }

    def mark_helpful(self, player_id: str, review_id: str) -> dict[str, Any]:
        """Toggles or records a helpful feedback vote on a review."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            review = session.scalar(select(QuizReview).where(QuizReview.id == review_id))
            if not review or review.is_hidden:
                raise LookupError("Review not found.")

            if review.player_id == player_id:
                raise ValueError("You cannot mark your own review as helpful.")

            existing = session.scalar(
                select(QuizReviewHelpful).where(
                    QuizReviewHelpful.review_id == review_id,
                    QuizReviewHelpful.player_id == player_id,
                )
            )
            if existing:
                # Toggle off
                session.delete(existing)
                review.helpful_count = max(0, review.helpful_count - 1)
                session.add(review)
                session.flush()
                return {"review_id": review_id, "helpful_count": review.helpful_count, "marked": False}

            helpful = QuizReviewHelpful(
                id=secrets.token_hex(16),
                review_id=review_id,
                player_id=player_id,
                created_at=now,
            )
            session.add(helpful)
            review.helpful_count += 1
            session.add(review)
            session.flush()
            return {"review_id": review_id, "helpful_count": review.helpful_count, "marked": True}

    def list_reviews(
        self,
        quiz_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Lists reviews for a quiz excluding hidden/moderated entries."""
        bounded_limit = max(1, min(limit, 50))
        with self._database.session() as session:
            stmt = (
                select(QuizReview)
                .options(selectinload(QuizReview.player))
                .where(QuizReview.quiz_id == quiz_id, QuizReview.is_hidden.is_(False))
                .order_by(QuizReview.helpful_count.desc(), QuizReview.created_at.desc())
            )
            total = session.scalar(
                select(func.count(QuizReview.id)).where(QuizReview.quiz_id == quiz_id, QuizReview.is_hidden.is_(False))
            ) or 0
            reviews = session.scalars(stmt.offset(offset).limit(bounded_limit)).all()

            return {
                "quiz_id": quiz_id,
                "total": total,
                "limit": bounded_limit,
                "offset": offset,
                "items": [
                    self._serialize_review(r, r.player.display_name if r.player else "Player")
                    for r in reviews
                ],
            }

    def _is_blocked(self, p1: str, p2: str) -> bool:
        if self._social_service is not None:
            return bool(self._social_service.is_blocked(p1, p2))
        with self._database.session() as s:
            query = select(PlayerBlock.id).where(
                ((PlayerBlock.blocker_id == p1) & (PlayerBlock.blocked_id == p2))
                | ((PlayerBlock.blocker_id == p2) & (PlayerBlock.blocked_id == p1))
            ).limit(1)
            return s.scalar(query) is not None

    def _serialize_review(self, r: QuizReview, reviewer_name: str) -> dict[str, Any]:
        return {
            "id": r.id,
            "quiz_id": r.quiz_id,
            "player_id": r.player_id,
            "reviewer_name": reviewer_name,
            "rating": r.rating,
            "review_text": r.review_text,
            "creator_response": r.creator_response,
            "creator_responded_at": r.creator_responded_at.isoformat() if r.creator_responded_at else None,
            "helpful_count": r.helpful_count,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }

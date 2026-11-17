"""Public community discovery feeds, search, and category filtering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from quizverse.persistence.db import Database
from quizverse.persistence.models import CreatorProfile, Quiz, QuizPlay, QuizPublication


class DiscoveryService:
    """Manages public community feeds, curated discover trays, and search."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def get_discovery_overview(self) -> dict[str, Any]:
        """Provides consolidated discovery trays for the Community home screen."""
        return {
            "featured": self.get_featured_quizzes(limit=6),
            "trending": self.get_trending_quizzes(limit=6),
            "popular": self.get_popular_quizzes(limit=6),
            "recent": self.get_recent_quizzes(limit=6),
            "top_rated": self.get_top_rated_quizzes(limit=6),
        }

    def get_featured_quizzes(self, limit: int = 10) -> list[dict[str, Any]]:
        """Returns high-quality, verified or highly rated community quizzes."""
        bounded = max(1, min(limit, 50))
        with self._database.session() as session:
            query = (
                select(QuizPublication)
                .options(selectinload(QuizPublication.quiz), selectinload(QuizPublication.creator))
                .where(
                    QuizPublication.status == "published",
                    QuizPublication.visibility == "public",
                    or_(QuizPublication.quality_score >= 70.0, QuizPublication.average_rating >= 4.0),
                )
                .order_by(QuizPublication.average_rating.desc(), QuizPublication.total_plays.desc(), QuizPublication.id)
                .limit(bounded)
            )
            pubs = session.scalars(query).all()
            if not pubs:
                # Fallback to any published public quizzes
                query_fallback = (
                    select(QuizPublication)
                    .options(selectinload(QuizPublication.quiz), selectinload(QuizPublication.creator))
                    .where(
                        QuizPublication.status == "published",
                        QuizPublication.visibility == "public",
                    )
                    .order_by(QuizPublication.published_at.desc(), QuizPublication.id)
                    .limit(bounded)
                )
                pubs = session.scalars(query_fallback).all()
            return [self._serialize_discovery_item(p, session) for p in pubs]

    def get_trending_quizzes(self, limit: int = 10) -> list[dict[str, Any]]:
        """Returns trending quizzes based on authoritative recent plays within last 7 days."""
        bounded = max(1, min(limit, 50))
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=7)

        with self._database.session() as session:
            # Query quiz IDs with recent plays
            recent_ids = session.scalars(
                select(QuizPlay.quiz_id)
                .where(QuizPlay.completed_at >= cutoff)
                .group_by(QuizPlay.quiz_id)
                .order_by(func.count(QuizPlay.id).desc())
                .limit(bounded * 2)
            ).all()

            if recent_ids:
                pubs = session.scalars(
                    select(QuizPublication)
                    .options(selectinload(QuizPublication.quiz), selectinload(QuizPublication.creator))
                    .where(
                        QuizPublication.quiz_id.in_(recent_ids),
                        QuizPublication.status == "published",
                        QuizPublication.visibility == "public",
                    )
                    .limit(bounded)
                ).all()
            else:
                pubs = []

            if not pubs:
                # Stable fallback to most played
                pubs = session.scalars(
                    select(QuizPublication)
                    .options(selectinload(QuizPublication.quiz), selectinload(QuizPublication.creator))
                    .where(
                        QuizPublication.status == "published",
                        QuizPublication.visibility == "public",
                    )
                    .order_by(QuizPublication.total_plays.desc(), QuizPublication.id)
                    .limit(bounded)
                ).all()

            return [self._serialize_discovery_item(p, session) for p in pubs]

    def get_popular_quizzes(self, limit: int = 10) -> list[dict[str, Any]]:
        """Returns all-time most played public community quizzes."""
        bounded = max(1, min(limit, 50))
        with self._database.session() as session:
            pubs = session.scalars(
                select(QuizPublication)
                .options(selectinload(QuizPublication.quiz), selectinload(QuizPublication.creator))
                .where(
                    QuizPublication.status == "published",
                    QuizPublication.visibility == "public",
                )
                .order_by(QuizPublication.total_plays.desc(), QuizPublication.id)
                .limit(bounded)
            ).all()
            return [self._serialize_discovery_item(p, session) for p in pubs]

    def get_recent_quizzes(self, limit: int = 10) -> list[dict[str, Any]]:
        """Returns the most recently published community quizzes."""
        bounded = max(1, min(limit, 50))
        with self._database.session() as session:
            pubs = session.scalars(
                select(QuizPublication)
                .options(selectinload(QuizPublication.quiz), selectinload(QuizPublication.creator))
                .where(
                    QuizPublication.status == "published",
                    QuizPublication.visibility == "public",
                )
                .order_by(QuizPublication.published_at.desc(), QuizPublication.id)
                .limit(bounded)
            ).all()
            return [self._serialize_discovery_item(p, session) for p in pubs]

    def get_top_rated_quizzes(self, limit: int = 10) -> list[dict[str, Any]]:
        """Returns top rated quizzes with at least 1 verified review."""
        bounded = max(1, min(limit, 50))
        with self._database.session() as session:
            pubs = session.scalars(
                select(QuizPublication)
                .options(selectinload(QuizPublication.quiz), selectinload(QuizPublication.creator))
                .where(
                    QuizPublication.status == "published",
                    QuizPublication.visibility == "public",
                    QuizPublication.total_reviews >= 1,
                )
                .order_by(QuizPublication.average_rating.desc(), QuizPublication.total_reviews.desc(), QuizPublication.id)
                .limit(bounded)
            ).all()
            return [self._serialize_discovery_item(p, session) for p in pubs]

    def search_quizzes(
        self,
        *,
        query: str | None = None,
        category: str | None = None,
        difficulty: str | None = None,
        min_rating: float | None = None,
        sort_by: str = "recent",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Performs public search and multi-facet filtering with strict private isolation."""
        bounded_limit = max(1, min(limit, 50))
        bounded_offset = max(0, offset)

        with self._database.session() as session:
            stmt = (
                select(QuizPublication)
                .join(Quiz, Quiz.id == QuizPublication.quiz_id)
                .outerjoin(CreatorProfile, CreatorProfile.id == QuizPublication.creator_id)
                .options(selectinload(QuizPublication.quiz), selectinload(QuizPublication.creator))
                .where(
                    QuizPublication.status == "published",
                    QuizPublication.visibility == "public",
                )
            )

            if query:
                term = f"%{query.strip().lower()}%"
                stmt = stmt.where(
                    or_(
                        func.lower(Quiz.title).like(term),
                        func.lower(Quiz.description).like(term),
                        func.lower(Quiz.category).like(term),
                        func.lower(CreatorProfile.creator_display_name).like(term),
                    )
                )

            if category:
                stmt = stmt.where(func.lower(Quiz.category) == category.strip().lower())

            if difficulty:
                stmt = stmt.where(Quiz.difficulty == difficulty.strip().lower())

            if min_rating is not None:
                stmt = stmt.where(QuizPublication.average_rating >= float(min_rating))

            # Ordering
            if sort_by == "popular":
                stmt = stmt.order_by(QuizPublication.total_plays.desc(), QuizPublication.id)
            elif sort_by == "top_rated":
                stmt = stmt.order_by(QuizPublication.average_rating.desc(), QuizPublication.total_reviews.desc(), QuizPublication.id)
            else:  # recent
                stmt = stmt.order_by(QuizPublication.published_at.desc(), QuizPublication.id)

            # Total matching count
            subq = stmt.order_by(None).subquery()
            count_stmt = select(func.count()).select_from(subq)
            total = session.scalar(count_stmt) or 0

            # Page slice
            pubs = session.scalars(stmt.offset(bounded_offset).limit(bounded_limit)).all()
            items = [self._serialize_discovery_item(p, session) for p in pubs]

            return {
                "items": items,
                "total": total,
                "limit": bounded_limit,
                "offset": bounded_offset,
            }

    def _serialize_discovery_item(self, p: QuizPublication, session: Any) -> dict[str, Any]:
        quiz = p.quiz
        creator_profile = session.scalar(
            select(CreatorProfile).where(CreatorProfile.id == p.creator_id)
        )
        return {
            "quiz_id": p.quiz_id,
            "title": quiz.title if quiz else "Untitled Quiz",
            "description": quiz.description if quiz else None,
            "category": quiz.category if quiz else "General",
            "difficulty": quiz.difficulty if quiz else "medium",
            "creator_id": p.creator_id,
            "creator_display_name": (
                creator_profile.creator_display_name if creator_profile else "Creator"
            ),
            "creator_specializations": creator_profile.creator_specializations if creator_profile else [],
            "total_plays": p.total_plays,
            "average_rating": round(p.average_rating, 2),
            "total_reviews": p.total_reviews,
            "quality_score": p.quality_score,
            "published_at": p.published_at.isoformat() if p.published_at else None,
            "share_slug": p.share_slug,
        }

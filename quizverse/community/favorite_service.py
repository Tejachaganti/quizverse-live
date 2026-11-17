"""Player private bookmarking and favorite quizzes."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from quizverse.persistence.db import Database
from quizverse.persistence.models import CreatorProfile, Quiz, QuizFavorite, QuizPublication


class FavoriteService:
    """Manages player personal quiz bookmarks with strict privacy isolation."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def add_favorite(self, player_id: str, quiz_id: str) -> dict[str, Any]:
        """Bookmarks a quiz for quick access."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            quiz = session.scalar(select(Quiz).where(Quiz.id == quiz_id))
            if not quiz:
                raise LookupError("Quiz not found.")

            existing = session.scalar(
                select(QuizFavorite).where(QuizFavorite.player_id == player_id, QuizFavorite.quiz_id == quiz_id)
            )
            if existing:
                return {"status": "already_favorited", "quiz_id": quiz_id}

            fav = QuizFavorite(
                id=secrets.token_hex(16),
                player_id=player_id,
                quiz_id=quiz_id,
                created_at=now,
            )
            session.add(fav)
            session.flush()
            return {"status": "favorited", "quiz_id": quiz_id, "created_at": now.isoformat()}

    def remove_favorite(self, player_id: str, quiz_id: str) -> dict[str, Any]:
        """Removes a quiz from personal bookmarks."""
        with self._database.sessions.begin() as session:
            existing = session.scalar(
                select(QuizFavorite).where(QuizFavorite.player_id == player_id, QuizFavorite.quiz_id == quiz_id)
            )
            if not existing:
                return {"status": "not_favorited", "quiz_id": quiz_id}

            session.delete(existing)
            session.flush()
            return {"status": "unfavorited", "quiz_id": quiz_id}

    def is_favorite(self, player_id: str, quiz_id: str) -> bool:
        """Checks if a quiz is in player favorites."""
        with self._database.session() as session:
            exists = session.scalar(
                select(QuizFavorite.id).where(
                    QuizFavorite.player_id == player_id,
                    QuizFavorite.quiz_id == quiz_id,
                ).limit(1)
            )
            return exists is not None

    def list_favorites(self, player_id: str, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        """Lists bookmarked quizzes for the authenticated player only."""
        bounded_limit = max(1, min(limit, 50))
        with self._database.session() as session:
            favs = session.scalars(
                select(QuizFavorite)
                .options(selectinload(QuizFavorite.quiz))
                .where(QuizFavorite.player_id == player_id)
                .order_by(QuizFavorite.created_at.desc())
                .offset(offset)
                .limit(bounded_limit)
            ).all()

            results = []
            for f in favs:
                quiz = f.quiz
                pub = session.scalar(select(QuizPublication).where(QuizPublication.quiz_id == f.quiz_id))
                creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == pub.creator_id)) if pub else None

                results.append({
                    "favorite_id": f.id,
                    "quiz_id": f.quiz_id,
                    "title": quiz.title if quiz else "Untitled Quiz",
                    "description": quiz.description if quiz else None,
                    "category": quiz.category if quiz else "General",
                    "difficulty": quiz.difficulty if quiz else "medium",
                    "creator_id": pub.creator_id if pub else None,
                    "creator_display_name": creator.creator_display_name if creator else "Creator",
                    "average_rating": pub.average_rating if pub else 0.0,
                    "total_plays": pub.total_plays if pub else 0,
                    "favorited_at": f.created_at.isoformat(),
                })
            return results

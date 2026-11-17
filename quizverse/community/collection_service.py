"""Quiz collections, playlists, and ordered curriculum sets."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    CreatorProfile,
    PlayerProfile,
    Quiz,
    QuizCollection,
    QuizCollectionItem,
    QuizPublication,
)


class CollectionService:
    """Manages reusable quiz playlists and subject curriculums with authorization."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create_collection(
        self,
        owner_id: str,
        title: str,
        description: str | None = None,
        visibility: str = "public",
        cover_url: str | None = None,
    ) -> dict[str, Any]:
        """Creates a new quiz playlist collection."""
        clean_title = title.strip()
        if not clean_title or len(clean_title) > 128:
            raise ValueError("Collection title must be between 1 and 128 characters.")

        if visibility not in {"public", "private", "unlisted"}:
            raise ValueError("Visibility must be 'public', 'private', or 'unlisted'.")

        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            col = QuizCollection(
                id=secrets.token_hex(16),
                owner_id=owner_id,
                title=clean_title,
                description=description.strip() if description else None,
                visibility=visibility,
                cover_url=cover_url.strip() if cover_url else None,
                item_count=0,
                created_at=now,
                updated_at=now,
            )
            session.add(col)
            session.flush()
            return self._serialize_collection(col, [])

    def update_collection(
        self,
        owner_id: str,
        collection_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        visibility: str | None = None,
        cover_url: str | None = None,
    ) -> dict[str, Any]:
        """Updates collection metadata ensuring owner authorization."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            col = session.scalar(select(QuizCollection).where(QuizCollection.id == collection_id))
            if not col:
                raise LookupError("Collection not found.")
            if col.owner_id != owner_id:
                raise PermissionError("You are not authorized to update this collection.")

            if title is not None:
                clean_title = title.strip()
                if not clean_title or len(clean_title) > 128:
                    raise ValueError("Collection title must be between 1 and 128 characters.")
                col.title = clean_title

            if description is not None:
                col.description = description.strip() or None

            if visibility is not None:
                if visibility not in {"public", "private", "unlisted"}:
                    raise ValueError("Visibility must be 'public', 'private', or 'unlisted'.")
                col.visibility = visibility

            if cover_url is not None:
                col.cover_url = cover_url.strip() or None

            col.updated_at = now
            session.add(col)
            session.flush()

            items = self._load_collection_items(session, collection_id)
            return self._serialize_collection(col, items)

    def delete_collection(self, owner_id: str, collection_id: str) -> dict[str, Any]:
        """Deletes collection and its item associations."""
        with self._database.sessions.begin() as session:
            col = session.scalar(select(QuizCollection).where(QuizCollection.id == collection_id))
            if not col:
                raise LookupError("Collection not found.")
            if col.owner_id != owner_id:
                raise PermissionError("You are not authorized to delete this collection.")

            session.delete(col)
            session.flush()
            return {"status": "deleted", "collection_id": collection_id}

    def add_quiz_to_collection(
        self,
        owner_id: str,
        collection_id: str,
        quiz_id: str,
        position: int | None = None,
    ) -> dict[str, Any]:
        """Adds a quiz to collection ensuring authorization and duplicate protection."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            col = session.scalar(select(QuizCollection).where(QuizCollection.id == collection_id))
            if not col or col.owner_id != owner_id:
                raise PermissionError("Collection not found or unauthorized.")

            # Validate quiz exists
            quiz = session.scalar(select(Quiz).where(Quiz.id == quiz_id))
            if not quiz:
                raise LookupError("Quiz not found.")

            # Ensure quiz is public or owned by collection owner
            pub = session.scalar(select(QuizPublication).where(QuizPublication.quiz_id == quiz_id))
            if pub and pub.visibility == "private" and pub.creator_id != owner_id:
                raise PermissionError("Cannot add another user's private quiz to a collection.")

            # Check if already added
            existing = session.scalar(
                select(QuizCollectionItem).where(
                    QuizCollectionItem.collection_id == collection_id,
                    QuizCollectionItem.quiz_id == quiz_id,
                )
            )
            if existing:
                items = self._load_collection_items(session, collection_id)
                return self._serialize_collection(col, items)

            max_pos = session.scalar(
                select(func.max(QuizCollectionItem.position)).where(QuizCollectionItem.collection_id == collection_id)
            )
            next_pos = (max_pos + 1) if max_pos is not None else 0
            actual_pos = position if position is not None else next_pos

            item = QuizCollectionItem(
                id=secrets.token_hex(16),
                collection_id=collection_id,
                quiz_id=quiz_id,
                position=actual_pos,
                created_at=now,
            )
            session.add(item)
            session.flush()

            col.item_count = session.scalar(
                select(func.count(QuizCollectionItem.id)).where(QuizCollectionItem.collection_id == collection_id)
            ) or 0
            col.updated_at = now
            session.add(col)
            session.flush()

            items = self._load_collection_items(session, collection_id)
            return self._serialize_collection(col, items)

    def remove_quiz_from_collection(
        self,
        owner_id: str,
        collection_id: str,
        quiz_id: str,
    ) -> dict[str, Any]:
        """Removes a quiz from collection."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            col = session.scalar(select(QuizCollection).where(QuizCollection.id == collection_id))
            if not col or col.owner_id != owner_id:
                raise PermissionError("Collection not found or unauthorized.")

            item = session.scalar(
                select(QuizCollectionItem).where(
                    QuizCollectionItem.collection_id == collection_id,
                    QuizCollectionItem.quiz_id == quiz_id,
                )
            )
            if not item:
                items = self._load_collection_items(session, collection_id)
                return self._serialize_collection(col, items)

            session.delete(item)
            session.flush()

            col.item_count = max(
                0,
                session.scalar(
                    select(func.count(QuizCollectionItem.id)).where(QuizCollectionItem.collection_id == collection_id)
                ),
            )
            col.updated_at = now
            session.add(col)
            session.flush()

            items = self._load_collection_items(session, collection_id)
            return self._serialize_collection(col, items)

    def reorder_collection(
        self,
        owner_id: str,
        collection_id: str,
        ordered_quiz_ids: list[str],
    ) -> dict[str, Any]:
        """Reorders quizzes inside a collection."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            col = session.scalar(select(QuizCollection).where(QuizCollection.id == collection_id))
            if not col or col.owner_id != owner_id:
                raise PermissionError("Collection not found or unauthorized.")

            items = session.scalars(
                select(QuizCollectionItem).where(QuizCollectionItem.collection_id == collection_id)
            ).all()
            item_map = {item.quiz_id: item for item in items}

            for idx, qid in enumerate(ordered_quiz_ids):
                if qid in item_map:
                    item_map[qid].position = idx
                    session.add(item_map[qid])

            col.updated_at = now
            session.add(col)
            session.flush()

            items = self._load_collection_items(session, collection_id)
            return self._serialize_collection(col, items)

    def get_collection(self, collection_id: str, viewer_id: str | None = None) -> dict[str, Any]:
        """Retrieves collection enforcing visibility privacy rules."""
        with self._database.session() as session:
            col = session.scalar(
                select(QuizCollection)
                .options(selectinload(QuizCollection.owner))
                .where(QuizCollection.id == collection_id)
            )
            if not col:
                raise LookupError("Collection not found.")

            if col.visibility == "private" and col.owner_id != viewer_id:
                raise PermissionError("This quiz collection is private.")

            items = self._load_collection_items(session, collection_id)
            return self._serialize_collection(col, items)

    def list_collections(
        self,
        *,
        owner_id: str | None = None,
        visibility: str | None = "public",
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Lists public or owner collections."""
        bounded_limit = max(1, min(limit, 50))
        with self._database.session() as session:
            query = select(QuizCollection).options(selectinload(QuizCollection.owner))
            if owner_id:
                query = query.where(QuizCollection.owner_id == owner_id)
            if visibility:
                query = query.where(QuizCollection.visibility == visibility)

            query = query.order_by(QuizCollection.updated_at.desc()).offset(offset).limit(bounded_limit)
            collections = session.scalars(query).all()

            results = []
            for c in collections:
                results.append(self._serialize_collection(c, []))
            return results

    def _load_collection_items(self, session: Any, collection_id: str) -> list[dict[str, Any]]:
        items = session.scalars(
            select(QuizCollectionItem)
            .options(selectinload(QuizCollectionItem.quiz))
            .where(QuizCollectionItem.collection_id == collection_id)
            .order_by(QuizCollectionItem.position.asc())
        ).all()

        results = []
        for i in items:
            quiz = i.quiz
            pub = session.scalar(select(QuizPublication).where(QuizPublication.quiz_id == i.quiz_id))
            results.append({
                "quiz_id": i.quiz_id,
                "position": i.position,
                "title": quiz.title if quiz else "Quiz",
                "category": quiz.category if quiz else "General",
                "difficulty": quiz.difficulty if quiz else "medium",
                "average_rating": pub.average_rating if pub else 0.0,
                "total_plays": pub.total_plays if pub else 0,
            })
        return results

    def _serialize_collection(self, c: QuizCollection, items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": c.id,
            "owner_id": c.owner_id,
            "owner_name": c.owner.display_name if c.owner else "Player",
            "title": c.title,
            "description": c.description,
            "visibility": c.visibility,
            "cover_url": c.cover_url,
            "item_count": c.item_count,
            "items": items,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }

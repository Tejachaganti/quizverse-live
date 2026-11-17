"""Creator profile management, follow relationships, and analytics."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    CreatorFollow,
    CreatorProfile,
    PlayerBlock,
    PlayerProfile,
    Quiz,
    QuizPlay,
    QuizPublication,
    QuizReview,
)


class CreatorService:
    """Manages pseudonymous creator identities, follows, and analytics."""

    def __init__(self, database: Database, notification_service: Any = None, social_service: Any = None) -> None:
        self._database = database
        self._notification_service = notification_service
        self._social_service = social_service

    def get_or_create_creator_profile(
        self,
        player_id: str,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """Fetches existing creator profile or creates default public creator identity."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            creator = session.scalar(
                select(CreatorProfile).where(CreatorProfile.id == player_id)
            )
            if creator is None:
                player = session.scalar(
                    select(PlayerProfile).where(PlayerProfile.id == player_id)
                )
                if not player:
                    raise ValueError("Player profile not found.")

                name = (display_name or player.display_name or "Creator").strip()[:32]
                creator = CreatorProfile(
                    id=player_id,
                    creator_display_name=name,
                    creator_bio=None,
                    creator_avatar_url=None,
                    creator_banner_url=None,
                    creator_specializations=[],
                    creator_visibility="public",
                    is_active=True,
                    published_quiz_count=0,
                    total_plays=0,
                    total_reviews=0,
                    average_rating=0.0,
                    follower_count=0,
                    following_count=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(creator)
                session.flush()

            return self._serialize_creator(creator)

    def update_creator_profile(
        self,
        player_id: str,
        *,
        display_name: str | None = None,
        bio: str | None = None,
        specializations: list[str] | None = None,
        visibility: str | None = None,
        avatar_url: str | None = None,
        banner_url: str | None = None,
    ) -> dict[str, Any]:
        """Updates creator identity metadata with strict input validation."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            creator = session.scalar(
                select(CreatorProfile).where(CreatorProfile.id == player_id)
            )
            if creator is None:
                # Auto-initialize if not existing yet
                player = session.scalar(select(PlayerProfile).where(PlayerProfile.id == player_id))
                if not player:
                    raise ValueError("Player profile not found.")
                creator = CreatorProfile(
                    id=player_id,
                    creator_display_name=(player.display_name or "Creator")[:32],
                    creator_specializations=[],
                    creator_visibility="public",
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(creator)
                session.flush()

            if display_name is not None:
                clean_name = display_name.strip()
                if not clean_name or len(clean_name) > 32:
                    raise ValueError("Creator display name must be between 1 and 32 characters.")
                creator.creator_display_name = clean_name

            if bio is not None:
                clean_bio = bio.strip()
                if len(clean_bio) > 1000:
                    raise ValueError("Creator bio must be at most 1000 characters.")
                creator.creator_bio = clean_bio or None

            if specializations is not None:
                clean_specs = [str(s).strip() for s in specializations if str(s).strip()][:10]
                creator.creator_specializations = clean_specs

            if visibility is not None:
                if visibility not in {"public", "private"}:
                    raise ValueError("Visibility must be 'public' or 'private'.")
                creator.creator_visibility = visibility

            if avatar_url is not None:
                creator.creator_avatar_url = avatar_url.strip()[:255] if avatar_url.strip() else None

            if banner_url is not None:
                creator.creator_banner_url = banner_url.strip()[:255] if banner_url.strip() else None

            creator.updated_at = now
            session.add(creator)
            session.flush()
            return self._serialize_creator(creator)

    def get_creator_profile(
        self,
        creator_id: str,
        viewer_id: str | None = None,
    ) -> dict[str, Any]:
        """Retrieves creator profile respecting visibility and blocking rules."""
        if viewer_id and self._is_blocked(creator_id, viewer_id):
            raise PermissionError("Access denied due to blocking.")

        with self._database.session() as session:
            creator = session.scalar(
                select(CreatorProfile).where(CreatorProfile.id == creator_id)
            )
            if not creator or not creator.is_active:
                raise LookupError("Creator profile not found.")

            if creator.creator_visibility == "private" and viewer_id != creator_id:
                raise PermissionError("This creator profile is private.")

            # Load recent published quizzes
            recent_pubs = session.scalars(
                select(QuizPublication)
                .options(selectinload(QuizPublication.quiz))
                .where(
                    QuizPublication.creator_id == creator_id,
                    QuizPublication.status == "published",
                    QuizPublication.visibility == "public",
                )
                .order_by(QuizPublication.published_at.desc())
                .limit(6)
            ).all()

            data = self._serialize_creator(creator)
            data["recent_quizzes"] = [
                {
                    "quiz_id": p.quiz_id,
                    "title": p.quiz.title if p.quiz else "Quiz",
                    "category": p.quiz.category if p.quiz else "General",
                    "difficulty": p.quiz.difficulty if p.quiz else "medium",
                    "total_plays": p.total_plays,
                    "average_rating": p.average_rating,
                    "total_reviews": p.total_reviews,
                    "published_at": p.published_at.isoformat() if p.published_at else None,
                }
                for p in recent_pubs
            ]

            if viewer_id:
                data["is_following"] = self.is_following(viewer_id, creator_id)
            else:
                data["is_following"] = False

            return data

    def list_creators(
        self,
        limit: int = 20,
        offset: int = 0,
        specialization: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lists active public creators ordered by popularity/followers."""
        bounded_limit = max(1, min(limit, 50))
        with self._database.session() as session:
            query = select(CreatorProfile).where(
                CreatorProfile.is_active.is_(True),
                CreatorProfile.creator_visibility == "public",
            )
            query = query.order_by(CreatorProfile.follower_count.desc(), CreatorProfile.total_plays.desc())
            query = query.offset(offset).limit(bounded_limit)
            creators = session.scalars(query).all()

            results = []
            for c in creators:
                if specialization:
                    specs = c.creator_specializations or []
                    if not any(specialization.lower() in s.lower() for s in specs):
                        continue
                results.append(self._serialize_creator(c))
            return results

    def follow_creator(self, follower_id: str, creator_id: str) -> dict[str, Any]:
        """Establishes a persistent follower relationship with safety and notification hooks."""
        if follower_id == creator_id:
            raise ValueError("You cannot follow yourself.")

        if self._is_blocked(follower_id, creator_id):
            raise PermissionError("Cannot follow this creator due to privacy or blocking.")

        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            # Check target creator exists
            creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == creator_id))
            if not creator:
                target_player = session.scalar(select(PlayerProfile).where(PlayerProfile.id == creator_id))
                if not target_player:
                    raise LookupError("Creator not found.")
                creator = CreatorProfile(
                    id=creator_id,
                    creator_display_name=(target_player.display_name or "Creator")[:32],
                    creator_specializations=[],
                    creator_visibility="public",
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(creator)
                session.flush()
            elif not creator.is_active:
                raise LookupError("Creator not found.")

            follower = session.scalar(select(PlayerProfile).where(PlayerProfile.id == follower_id))
            if not follower:
                raise LookupError("Follower profile not found.")

            # Check if already following
            existing = session.scalar(
                select(CreatorFollow).where(
                    CreatorFollow.follower_id == follower_id,
                    CreatorFollow.creator_id == creator_id,
                )
            )
            if existing:
                return {"status": "already_following", "follower_count": creator.follower_count}

            follow_record = CreatorFollow(
                id=secrets.token_hex(16),
                follower_id=follower_id,
                creator_id=creator_id,
                created_at=now,
            )
            session.add(follow_record)
            session.flush()

            # Update follower counts
            creator.follower_count = session.scalar(
                select(func.count(CreatorFollow.id)).where(CreatorFollow.creator_id == creator_id)
            ) or 0
            session.add(creator)

            # Update following count on follower's creator profile if it exists
            follower_creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == follower_id))
            if follower_creator:
                follower_creator.following_count = session.scalar(
                    select(func.count(CreatorFollow.id)).where(CreatorFollow.follower_id == follower_id)
                ) or 0
                session.add(follower_creator)

            follower_name = follower.display_name or "A player"

        # Dispatch notification to creator
        if self._notification_service is not None:
            self._notification_service.create_notification(
                recipient_id=creator_id,
                actor_id=follower_id,
                notification_type="creator_follow",
                title="New Follower!",
                body=f"{follower_name} started following your quizzes and creator activity.",
                target_type="creator",
                target_id=creator_id,
                deduplication_key=f"follow:{follower_id}:{creator_id}",
            )

        return {"status": "followed", "creator_id": creator_id, "follower_count": creator.follower_count}

    def unfollow_creator(self, follower_id: str, creator_id: str) -> dict[str, Any]:
        """Removes follower relationship and updates aggregate counters."""
        with self._database.sessions.begin() as session:
            existing = session.scalar(
                select(CreatorFollow).where(
                    CreatorFollow.follower_id == follower_id,
                    CreatorFollow.creator_id == creator_id,
                )
            )
            if not existing:
                return {"status": "not_following"}

            session.delete(existing)
            session.flush()

            creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == creator_id))
            if creator:
                creator.follower_count = max(
                    0,
                    session.scalar(
                        select(func.count(CreatorFollow.id)).where(CreatorFollow.creator_id == creator_id)
                    ),
                )
                session.add(creator)

            follower_creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == follower_id))
            if follower_creator:
                follower_creator.following_count = max(
                    0,
                    session.scalar(
                        select(func.count(CreatorFollow.id)).where(CreatorFollow.follower_id == follower_id)
                    ),
                )
                session.add(follower_creator)

            count = creator.follower_count if creator else 0
            return {"status": "unfollowed", "creator_id": creator_id, "follower_count": count}

    def is_following(self, follower_id: str, creator_id: str) -> bool:
        """Checks if follower follows creator."""
        with self._database.session() as session:
            exists = session.scalar(
                select(CreatorFollow.id).where(
                    CreatorFollow.follower_id == follower_id,
                    CreatorFollow.creator_id == creator_id,
                ).limit(1)
            )
            return exists is not None

    def list_followers(self, creator_id: str, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        """Lists followers of a creator."""
        bounded_limit = max(1, min(limit, 50))
        with self._database.session() as session:
            follows = session.scalars(
                select(CreatorFollow)
                .options(selectinload(CreatorFollow.follower))
                .where(CreatorFollow.creator_id == creator_id)
                .order_by(CreatorFollow.created_at.desc())
                .offset(offset)
                .limit(bounded_limit)
            ).all()

            return [
                {
                    "follower_id": f.follower_id,
                    "display_name": f.follower.display_name if f.follower else "Player",
                    "followed_at": f.created_at.isoformat(),
                }
                for f in follows
            ]

    def list_following(self, follower_id: str, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        """Lists creators followed by a player."""
        bounded_limit = max(1, min(limit, 50))
        with self._database.session() as session:
            follows = session.scalars(
                select(CreatorFollow)
                .options(selectinload(CreatorFollow.creator))
                .where(CreatorFollow.follower_id == follower_id)
                .order_by(CreatorFollow.created_at.desc())
                .offset(offset)
                .limit(bounded_limit)
            ).all()

            results = []
            for f in follows:
                creator_profile = session.scalar(
                    select(CreatorProfile).where(CreatorProfile.id == f.creator_id)
                )
                results.append(
                    {
                        "creator_id": f.creator_id,
                        "creator_display_name": (
                            creator_profile.creator_display_name
                            if creator_profile
                            else (f.creator.display_name if f.creator else "Creator")
                        ),
                        "specializations": creator_profile.creator_specializations if creator_profile else [],
                        "average_rating": creator_profile.average_rating if creator_profile else 0.0,
                        "follower_count": creator_profile.follower_count if creator_profile else 0,
                        "followed_at": f.created_at.isoformat(),
                    }
                )
            return results

    def get_creator_analytics(self, creator_id: str, time_window: str = "all_time") -> dict[str, Any]:
        """Computes verifiable creator analytics based on actual database records."""
        now = datetime.now(UTC)
        cutoff: datetime | None = None
        if time_window == "recent":
            cutoff = now - timedelta(days=2)
        elif time_window == "7_days":
            cutoff = now - timedelta(days=7)
        elif time_window == "30_days":
            cutoff = now - timedelta(days=30)

        with self._database.session() as session:
            creator = session.scalar(select(CreatorProfile).where(CreatorProfile.id == creator_id))
            if not creator:
                raise LookupError("Creator profile not found.")

            # Load creator's quizzes
            pubs_query = select(QuizPublication).options(selectinload(QuizPublication.quiz)).where(QuizPublication.creator_id == creator_id)
            publications = session.scalars(pubs_query).all()
            quiz_ids = [p.quiz_id for p in publications]

            # Plays in window
            plays_query = select(func.count(QuizPlay.id)).where(QuizPlay.quiz_id.in_(quiz_ids)) if quiz_ids else None
            if plays_query is not None and cutoff is not None:
                plays_query = plays_query.where(QuizPlay.completed_at >= cutoff)
            window_plays = session.scalar(plays_query) if plays_query is not None else 0

            # Reviews in window
            rev_query = select(func.count(QuizReview.id)).where(QuizReview.quiz_id.in_(quiz_ids), QuizReview.is_hidden.is_(False)) if quiz_ids else None
            if rev_query is not None and cutoff is not None:
                rev_query = rev_query.where(QuizReview.created_at >= cutoff)
            window_reviews = session.scalar(rev_query) if rev_query is not None else 0

            # Unique players
            uniq_query = select(func.count(func.distinct(QuizPlay.player_id))).where(QuizPlay.quiz_id.in_(quiz_ids)) if quiz_ids else None
            if uniq_query is not None and cutoff is not None:
                uniq_query = uniq_query.where(QuizPlay.completed_at >= cutoff)
            unique_players = session.scalar(uniq_query) if uniq_query is not None else 0

            # Top performing quizzes
            top_quizzes = sorted(publications, key=lambda p: (p.total_plays, p.average_rating), reverse=True)[:5]

            return {
                "creator_id": creator_id,
                "time_window": time_window,
                "total_published_quizzes": sum(1 for p in publications if p.status == "published"),
                "total_draft_quizzes": sum(1 for p in publications if p.status == "draft"),
                "total_plays": window_plays if cutoff else creator.total_plays,
                "unique_players": unique_players,
                "total_reviews": window_reviews if cutoff else creator.total_reviews,
                "average_rating": creator.average_rating,
                "follower_count": creator.follower_count,
                "popular_quizzes": [
                    {
                        "quiz_id": p.quiz_id,
                        "title": p.quiz.title if p.quiz else "Quiz",
                        "total_plays": p.total_plays,
                        "average_rating": p.average_rating,
                        "total_reviews": p.total_reviews,
                        "status": p.status,
                        "visibility": p.visibility,
                    }
                    for p in top_quizzes
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

    def _serialize_creator(self, c: CreatorProfile) -> dict[str, Any]:
        return {
            "creator_id": c.id,
            "creator_display_name": c.creator_display_name,
            "display_name": c.creator_display_name,
            "creator_bio": c.creator_bio,
            "bio": c.creator_bio,
            "creator_avatar_url": c.creator_avatar_url,
            "creator_banner_url": c.creator_banner_url,
            "specializations": c.creator_specializations or [],
            "creator_visibility": c.creator_visibility,
            "is_active": c.is_active,
            "published_quiz_count": c.published_quiz_count,
            "total_plays": c.total_plays,
            "total_reviews": c.total_reviews,
            "average_rating": round(c.average_rating, 2),
            "follower_count": c.follower_count,
            "following_count": c.following_count,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }

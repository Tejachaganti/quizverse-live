"""Centralized notification management, preferences, and social intelligence service."""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import and_, delete, desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    DirectConversation,
    FriendRequest,
    Friendship,
    League,
    LeagueSeason,
    MatchParticipant,
    NotificationPreference,
    PlayerBlock,
    PlayerNotification,
    PlayerProfile,
    PlayerRating,
    RematchChallenge,
    RoomInvitation,
    SeasonParticipant,
    Tournament,
    TournamentParticipant,
    utc_now,
)
from quizverse.persistence.social import BlockService, FriendService


class NotificationError(Exception):
    """Domain exception raised when notification actions violate safety, privacy, or validation rules."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# Notification types supported across QuizVerse Live
NOTIFICATION_CATEGORIES: dict[str, list[str]] = {
    "social": [
        "friend_request_received",
        "friend_request",
        "friend_accepted",
        "friend_request_accepted",
        "friendship_created",
        "friend_online",
    ],
    "invitations": [
        "room_invitation_received",
        "room_invitation",
        "room_invitation_accepted",
        "room_invitation_declined",
    ],
    "rematches": [
        "rematch_challenge_received",
        "rematch_challenge",
        "rematch_challenge_accepted",
        "rematch_challenge_declined",
    ],
    "tournaments": [
        "tournament_registered",
        "tournament_match_ready",
        "tournament_advancement",
        "tournament_elimination",
        "tournament_champion",
        "tournament_runner_up",
    ],
    "leagues": [
        "league_registered",
        "league_season_started",
        "league_match_ready",
        "league_podium",
    ],
    "achievements": [
        "achievement_unlocked",
        "rating_milestone",
        "personal_best",
        "badge_awarded",
        "tier_promoted",
    ],
    "messages": [
        "direct_message",
        "direct_message_received",
    ],
    "system": [
        "system",
        "announcement",
        "maintenance",
    ],
}

# Reverse mapping from notification type to preference column
NOTIFICATION_TYPE_TO_PREF_MAP: dict[str, str] = {
    "friend_request_received": "friend_requests",
    "friend_request": "friend_requests",
    "friend_accepted": "friendship_updates",
    "friend_request_accepted": "friendship_updates",
    "friendship_created": "friendship_updates",
    "friend_online": "friend_online",
    "room_invitation_received": "room_invitations",
    "room_invitation": "room_invitations",
    "room_invitation_accepted": "room_invitations",
    "room_invitation_declined": "room_invitations",
    "rematch_challenge_received": "rematch_challenges",
    "rematch_challenge": "rematch_challenges",
    "rematch_challenge_accepted": "rematch_challenges",
    "rematch_challenge_declined": "rematch_challenges",
    "direct_message": "direct_message_notifications",
    "direct_message_received": "direct_message_notifications",
    "tournament_registered": "tournament_updates",
    "tournament_match_ready": "tournament_updates",
    "tournament_advancement": "tournament_updates",
    "tournament_elimination": "tournament_updates",
    "tournament_champion": "tournament_updates",
    "tournament_runner_up": "tournament_updates",
    "league_registered": "league_updates",
    "league_season_started": "league_updates",
    "league_match_ready": "league_updates",
    "league_podium": "league_updates",
    "achievement_unlocked": "achievement_notifications",
    "badge_awarded": "achievement_notifications",
    "tier_promoted": "achievement_notifications",
    "rating_milestone": "rating_milestones",
    "personal_best": "personal_best_notifications",
}


class NotificationService:
    """Server-authoritative notification lifecycle, deduplication, retention, and delivery engine."""

    def __init__(
        self,
        database: Database,
        block_service: BlockService,
        friend_service: FriendService | None = None,
        *,
        presence_service: Any = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._database = database
        self._block_service = block_service
        self._friend_service = friend_service
        self.presence_service = presence_service
        self._clock = clock

    def _dispatch_task(self, coro: Any) -> None:
        if self.presence_service is None:
            if hasattr(coro, "close"):
                coro.close()
            return
        if hasattr(self.presence_service, "safe_dispatch"):
            self.presence_service.safe_dispatch(coro)
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            if hasattr(coro, "close"):
                coro.close()

    def _serialize_notification(self, notif: PlayerNotification, actor_name: str | None = None) -> dict[str, Any]:
        meta = {}
        if notif.metadata_json:
            try:
                meta = json.loads(notif.metadata_json)
            except Exception:
                meta = {}

        return {
            "id": notif.id,
            "recipient_id": notif.recipient_id,
            "actor_id": notif.actor_id,
            "actor_name": actor_name,
            "notification_type": notif.notification_type,
            "title": notif.title,
            "body": notif.body,
            "target_type": notif.target_type,
            "target_id": notif.target_id,
            "metadata": meta,
            "is_read": bool(notif.is_read),
            "read_at": notif.read_at.isoformat() if notif.read_at else None,
            "created_at": notif.created_at.isoformat(),
            "expires_at": notif.expires_at.isoformat() if notif.expires_at else None,
            "deduplication_key": notif.deduplication_key,
        }

    def _serialize_preferences(self, pref: NotificationPreference) -> dict[str, Any]:
        return {
            "player_id": pref.player_id,
            "friend_requests": bool(pref.friend_requests),
            "friendship_updates": bool(pref.friendship_updates),
            "friend_online": bool(pref.friend_online),
            "room_invitations": bool(pref.room_invitations),
            "rematch_challenges": bool(pref.rematch_challenges),
            "direct_message_notifications": bool(pref.direct_message_notifications),
            "tournament_updates": bool(pref.tournament_updates),
            "league_updates": bool(pref.league_updates),
            "achievement_notifications": bool(pref.achievement_notifications),
            "rating_milestones": bool(pref.rating_milestones),
            "personal_best_notifications": bool(pref.personal_best_notifications),
            "browser_sound": bool(pref.browser_sound),
            "visual_notifications": bool(pref.visual_notifications),
            "updated_at": pref.updated_at.isoformat(),
        }

    def get_preferences(self, player_id: str) -> dict[str, Any]:
        """Fetch notification preferences for player, creating defaults if not yet stored."""
        now = self._clock()
        with self._database.sessions.begin() as session:
            pref = session.scalar(
                select(NotificationPreference).where(NotificationPreference.player_id == player_id)
            )
            if pref is None:
                pref = NotificationPreference(
                    id=secrets.token_hex(16),
                    player_id=player_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(pref)
                session.flush()

            return self._serialize_preferences(pref)

    def update_preferences(self, player_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update configurable notification preference toggles."""
        valid_keys = {
            "friend_requests",
            "friendship_updates",
            "friend_online",
            "room_invitations",
            "rematch_challenges",
            "direct_message_notifications",
            "tournament_updates",
            "league_updates",
            "achievement_notifications",
            "rating_milestones",
            "personal_best_notifications",
            "browser_sound",
            "visual_notifications",
        }
        now = self._clock()

        with self._database.sessions.begin() as session:
            pref = session.scalar(
                select(NotificationPreference).where(NotificationPreference.player_id == player_id)
            )
            if pref is None:
                pref = NotificationPreference(
                    id=secrets.token_hex(16),
                    player_id=player_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(pref)
                session.flush()

            for key, val in updates.items():
                if key in valid_keys:
                    setattr(pref, key, bool(val))

            pref.updated_at = now
            session.add(pref)
            session.flush()
            serialized = self._serialize_preferences(pref)

        # Dispatch real-time preference update if presence service is active
        if self.presence_service is not None:
            self._dispatch_task(self.presence_service.send_notification_preferences_updated(player_id, serialized))

        return serialized

    def create_notification(
        self,
        recipient_id: str,
        notification_type: str,
        title: str,
        body: str,
        *,
        actor_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        deduplication_key: str | None = None,
        source_event_id: str | None = None,
        expires_in_days: int | None = None,
    ) -> dict[str, Any] | None:
        """Create and persist a server-authoritative player notification after safety and preference checks."""
        now = self._clock()

        # 1. Validation
        if not recipient_id or not recipient_id.strip():
            raise ValueError("Recipient ID cannot be empty.")
        if not title or not title.strip():
            raise ValueError("Notification title cannot be empty.")
        if not body or not body.strip():
            raise ValueError("Notification body cannot be empty.")

        # 2. Privacy & Blocking check
        if actor_id and actor_id == recipient_id:
            # Self-events do not notify self
            return None

        # 3. Check preferences and persistence
        with self._database.sessions.begin() as session:
            recipient = session.get(PlayerProfile, recipient_id)
            if recipient is None or recipient.status != "active":
                return None

            # Bi-directional block suppression
            if actor_id:
                if self._block_service.is_blocked(recipient_id, actor_id, session=session):
                    return None
                if self._block_service.is_blocked(actor_id, recipient_id, session=session):
                    return None

            # Check player preferences for this category
            pref_column = NOTIFICATION_TYPE_TO_PREF_MAP.get(notification_type)
            if pref_column:
                pref = session.scalar(
                    select(NotificationPreference).where(NotificationPreference.player_id == recipient_id)
                )
                if pref is not None and not getattr(pref, pref_column, True):
                    # Suppressed by player preference
                    return None

            # Deduplication check
            if deduplication_key:
                existing = session.scalar(
                    select(PlayerNotification).where(
                        PlayerNotification.recipient_id == recipient_id,
                        PlayerNotification.deduplication_key == deduplication_key,
                    )
                )
                if existing is not None:
                    # If this is a direct message notification, refresh text and update timestamp
                    if notification_type in NOTIFICATION_CATEGORIES["messages"]:
                        existing.body = body
                        existing.is_read = False
                        existing.read_at = None
                        existing.created_at = now
                        session.add(existing)
                        session.flush()
                        serialized = self._serialize_notification(existing)
                        self._dispatch_realtime(recipient_id, serialized)
                        return serialized
                    # For other idempotent events, return existing record
                    return self._serialize_notification(existing)

            # Expiry policy
            if expires_in_days is not None:
                expires_at = now + timedelta(days=expires_in_days)
            elif notification_type in NOTIFICATION_CATEGORIES["invitations"] or notification_type in NOTIFICATION_CATEGORIES["rematches"]:
                expires_at = now + timedelta(days=30)
            elif notification_type in NOTIFICATION_CATEGORIES["achievements"]:
                expires_at = None  # Achievements preserved
            else:
                expires_at = now + timedelta(days=90)

            actor_name = None
            if actor_id:
                actor = session.get(PlayerProfile, actor_id)
                if actor:
                    actor_name = actor.display_name

            notification = PlayerNotification(
                id=secrets.token_hex(16),
                recipient_id=recipient_id,
                actor_id=actor_id,
                notification_type=notification_type,
                title=title.strip(),
                body=body.strip(),
                target_type=target_type,
                target_id=target_id,
                metadata_json=json.dumps(metadata) if metadata else None,
                is_read=False,
                read_at=None,
                created_at=now,
                expires_at=expires_at,
                deduplication_key=deduplication_key,
                source_event_id=source_event_id,
            )
            try:
                session.add(notification)
                session.flush()
            except IntegrityError:
                if deduplication_key:
                    # Race condition on unique constraint: return existing
                    session.rollback()
                    existing = session.scalar(
                        select(PlayerNotification).where(
                            PlayerNotification.recipient_id == recipient_id,
                            PlayerNotification.deduplication_key == deduplication_key,
                        )
                    )
                    if existing:
                        return self._serialize_notification(existing)
                raise

            serialized = self._serialize_notification(notification, actor_name=actor_name)

        # 4. Dispatch real-time WebSocket event after database commit
        self._dispatch_realtime(recipient_id, serialized)
        return serialized

    def _dispatch_realtime(self, recipient_id: str, notification: dict[str, Any]) -> None:
        """Helper to safely broadcast new notification and updated unread count to recipient."""
        if self.presence_service is None:
            return
        unread_count = self.get_unread_count(recipient_id)
        self._dispatch_task(self.presence_service.send_notification_received(recipient_id, notification))
        self._dispatch_task(self.presence_service.send_notification_unread_count(recipient_id, unread_count))

    def list_notifications(
        self,
        recipient_id: str,
        *,
        category: str | None = None,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Query non-expired notifications with filtering, pagination, and unread counts."""
        now = self._clock()
        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        with self._database.session() as session:
            # Build filters
            filters = [
                PlayerNotification.recipient_id == recipient_id,
                or_(
                    PlayerNotification.expires_at.is_(None),
                    PlayerNotification.expires_at > now,
                ),
            ]

            if unread_only:
                filters.append(PlayerNotification.is_read.is_(False))

            if category and category in NOTIFICATION_CATEGORIES:
                types = NOTIFICATION_CATEGORIES[category]
                filters.append(PlayerNotification.notification_type.in_(types))

            # Query count
            total = session.scalar(select(func.count(PlayerNotification.id)).where(*filters)) or 0

            # Query page
            items = session.scalars(
                select(PlayerNotification)
                .where(*filters)
                .order_by(desc(PlayerNotification.created_at))
                .limit(limit)
                .offset(offset)
            ).all()

            # Actor names lookup
            actor_ids = {n.actor_id for n in items if n.actor_id}
            actors_map = {}
            if actor_ids:
                actors = session.scalars(
                    select(PlayerProfile).where(PlayerProfile.id.in_(actor_ids))
                ).all()
                actors_map = {a.id: a.display_name for a in actors}

            notifications = [
                self._serialize_notification(n, actor_name=actors_map.get(n.actor_id))
                for n in items
            ]

            unread_count = self.get_unread_count(recipient_id)

            return {
                "notifications": notifications,
                "total": total,
                "limit": limit,
                "offset": offset,
                "unread_count": unread_count,
            }

    def get_unread_count(self, recipient_id: str) -> int:
        """Count unread, non-expired notifications for a player."""
        now = self._clock()
        with self._database.session() as session:
            count = session.scalar(
                select(func.count(PlayerNotification.id)).where(
                    PlayerNotification.recipient_id == recipient_id,
                    PlayerNotification.is_read.is_(False),
                    or_(
                        PlayerNotification.expires_at.is_(None),
                        PlayerNotification.expires_at > now,
                    ),
                )
            )
            return count or 0

    def get_summary(self, recipient_id: str) -> dict[str, Any]:
        """Get high-level unread breakdown by category, recent volume, and preferences."""
        now = self._clock()
        with self._database.session() as session:
            unread_total = self.get_unread_count(recipient_id)

            # Category counts
            category_counts: dict[str, int] = {}
            for cat_name, cat_types in NOTIFICATION_CATEGORIES.items():
                c = session.scalar(
                    select(func.count(PlayerNotification.id)).where(
                        PlayerNotification.recipient_id == recipient_id,
                        PlayerNotification.is_read.is_(False),
                        PlayerNotification.notification_type.in_(cat_types),
                        or_(
                            PlayerNotification.expires_at.is_(None),
                            PlayerNotification.expires_at > now,
                        ),
                    )
                ) or 0
                if c > 0:
                    category_counts[cat_name] = c

            # Most recent unread notification
            latest = session.scalar(
                select(PlayerNotification)
                .where(
                    PlayerNotification.recipient_id == recipient_id,
                    PlayerNotification.is_read.is_(False),
                    or_(
                        PlayerNotification.expires_at.is_(None),
                        PlayerNotification.expires_at > now,
                    ),
                )
                .order_by(desc(PlayerNotification.created_at))
                .limit(1)
            )

            latest_dict = self._serialize_notification(latest) if latest else None
            prefs = self.get_preferences(recipient_id)

            return {
                "unread_count": unread_total,
                "category_counts": category_counts,
                "latest_unread": latest_dict,
                "preferences": prefs,
            }

    def mark_read(self, recipient_id: str, notification_id: str) -> dict[str, Any]:
        """Mark a single notification as read."""
        now = self._clock()
        with self._database.sessions.begin() as session:
            notif = session.get(PlayerNotification, notification_id)
            if not notif or notif.recipient_id != recipient_id:
                raise NotificationError("not_found", "Notification not found.", 404)

            notif.is_read = True
            notif.read_at = now
            session.add(notif)

            actor_name = None
            if notif.actor_id:
                actor = session.get(PlayerProfile, notif.actor_id)
                if actor:
                    actor_name = actor.display_name

            serialized = self._serialize_notification(notif, actor_name=actor_name)

        if self.presence_service is not None:
            unread_count = self.get_unread_count(recipient_id)
            self._dispatch_task(self.presence_service.send_notification_read(recipient_id, {"id": notification_id}))
            self._dispatch_task(self.presence_service.send_notification_unread_count(recipient_id, unread_count))

        return serialized

    def mark_unread(self, recipient_id: str, notification_id: str) -> dict[str, Any]:
        """Mark a single notification as unread."""
        with self._database.sessions.begin() as session:
            notif = session.get(PlayerNotification, notification_id)
            if not notif or notif.recipient_id != recipient_id:
                raise NotificationError("not_found", "Notification not found.", 404)

            notif.is_read = False
            notif.read_at = None
            session.add(notif)

            actor_name = None
            if notif.actor_id:
                actor = session.get(PlayerProfile, notif.actor_id)
                if actor:
                    actor_name = actor.display_name

            serialized = self._serialize_notification(notif, actor_name=actor_name)

        if self.presence_service is not None:
            unread_count = self.get_unread_count(recipient_id)
            self._dispatch_task(self.presence_service.send_notification_unread_count(recipient_id, unread_count))

        return serialized

    def mark_all_read(self, recipient_id: str, category: str | None = None) -> int:
        """Mark all notifications (or all in a category) for recipient as read."""
        now = self._clock()
        with self._database.sessions.begin() as session:
            stmt = (
                update(PlayerNotification)
                .where(
                    PlayerNotification.recipient_id == recipient_id,
                    PlayerNotification.is_read.is_(False),
                )
                .values(is_read=True, read_at=now)
            )
            if category and category in NOTIFICATION_CATEGORIES:
                types = NOTIFICATION_CATEGORIES[category]
                stmt = stmt.where(PlayerNotification.notification_type.in_(types))

            result = session.execute(stmt)
            updated_count = result.rowcount

        if self.presence_service is not None:
            self._dispatch_task(self.presence_service.send_notification_unread_count(recipient_id, 0))

        return updated_count

    def delete_notification(self, recipient_id: str, notification_id: str) -> bool:
        """Dismiss or delete a single notification."""
        with self._database.sessions.begin() as session:
            notif = session.get(PlayerNotification, notification_id)
            if not notif or notif.recipient_id != recipient_id:
                return False

            session.delete(notif)

        if self.presence_service is not None:
            unread_count = self.get_unread_count(recipient_id)
            self._dispatch_task(self.presence_service.send_notification_deleted(recipient_id, {"id": notification_id}))
            self._dispatch_task(self.presence_service.send_notification_unread_count(recipient_id, unread_count))

        return True

    def delete_read_notifications(self, recipient_id: str) -> int:
        """Bulk clear all read notifications for a player."""
        with self._database.sessions.begin() as session:
            stmt = delete(PlayerNotification).where(
                PlayerNotification.recipient_id == recipient_id,
                PlayerNotification.is_read.is_(True),
            )
            result = session.execute(stmt)
            deleted_count = result.rowcount

        if self.presence_service is not None:
            unread_count = self.get_unread_count(recipient_id)
            self._dispatch_task(self.presence_service.send_notification_unread_count(recipient_id, unread_count))

        return deleted_count

    def prune_retained_notifications(self, batch_size: int = 1000) -> int:
        """Safely prune expired, 90-day global, and 30-day read notifications in bounded batches while preserving achievements."""
        now = self._clock()
        global_cutoff = now - timedelta(days=90)
        read_cutoff = now - timedelta(days=30)
        total_pruned = 0
        achievement_types = NOTIFICATION_CATEGORIES["achievements"]

        with self._database.sessions.begin() as session:
            expired_ids = session.scalars(
                select(PlayerNotification.id)
                .where(
                    PlayerNotification.notification_type.not_in(achievement_types),
                    or_(
                        and_(PlayerNotification.expires_at.is_not(None), PlayerNotification.expires_at <= now),
                        PlayerNotification.created_at <= global_cutoff,
                        and_(PlayerNotification.is_read.is_(True), PlayerNotification.created_at <= read_cutoff),
                    ),
                )
                .limit(batch_size)
            ).all()

            if expired_ids:
                stmt = delete(PlayerNotification).where(PlayerNotification.id.in_(expired_ids))
                result = session.execute(stmt)
                total_pruned = result.rowcount

        return total_pruned

    def prune_expired_notifications(self, batch_size: int = 1000) -> int:
        return self.prune_retained_notifications(batch_size=batch_size)


class SocialIntelligenceService:
    """Computes authorized, privacy-respecting social activity summaries from authoritative records."""

    def __init__(
        self,
        database: Database,
        friend_service: FriendService,
        block_service: BlockService,
        *,
        presence_service: Any = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._database = database
        self._friend_service = friend_service
        self._block_service = block_service
        self.presence_service = presence_service
        self._clock = clock

    def get_social_overview(self, player_id: str) -> dict[str, Any]:
        """Compile a comprehensive Social Intelligence overview for the authenticated player."""
        now = self._clock()

        with self._database.session() as session:
            # 1. Friends list and online count
            friends = self._friend_service.list_friends(player_id)
            friend_count = len(friends)
            online_friends = 0
            friend_ids = {f.get("player_id") or f.get("friend_id") for f in friends if f.get("player_id") or f.get("friend_id")}

            if self.presence_service is not None:
                for f in friends:
                    status = f.get("presence", {}).get("status", "offline")
                    if status in ("online", "in_lobby", "in_match"):
                        online_friends += 1

            # 2. Pending Friend Requests
            pending_requests = session.scalar(
                select(func.count(FriendRequest.id)).where(
                    FriendRequest.recipient_id == player_id,
                    FriendRequest.status == "pending",
                )
            ) or 0

            # 3. Pending Room Invitations
            pending_invitations = session.scalar(
                select(func.count(RoomInvitation.id)).where(
                    RoomInvitation.invitee_id == player_id,
                    RoomInvitation.status == "pending",
                    RoomInvitation.expires_at > now,
                )
            ) or 0

            # 4. Pending Rematch Challenges
            pending_challenges = session.scalar(
                select(func.count(RematchChallenge.id)).where(
                    RematchChallenge.opponent_id == player_id,
                    RematchChallenge.status == "pending",
                    RematchChallenge.expires_at > now,
                )
            ) or 0

            # 5. Unread Direct Messages across conversations
            conv_rows = session.scalars(
                select(DirectConversation).where(
                    or_(
                        DirectConversation.player_one_id == player_id,
                        DirectConversation.player_two_id == player_id,
                    )
                )
            ).all()
            unread_dms = sum(
                c.player_one_unread_count if c.player_one_id == player_id else c.player_two_unread_count
                for c in conv_rows
            )

            # 6. Recent Friend & Social Activity (Authorized, non-intrusive)
            recent_activity: list[dict[str, Any]] = []
            if friend_ids:
                # A. Recent Friendships established (last 7 days)
                seven_days_ago = now - timedelta(days=7)
                recent_friendships = session.scalars(
                    select(Friendship)
                    .where(
                        or_(
                            Friendship.player_one_id.in_(friend_ids),
                            Friendship.player_two_id.in_(friend_ids),
                        ),
                        Friendship.created_at >= seven_days_ago,
                    )
                    .order_by(desc(Friendship.created_at))
                    .limit(5)
                ).all()

                for f in recent_friendships:
                    p1_id, p2_id = f.player_one_id, f.player_two_id
                    # Never reveal blocked players
                    if self._block_service.is_blocked(player_id, p1_id, session=session) or self._block_service.is_blocked(player_id, p2_id, session=session):
                        continue
                    p1 = session.get(PlayerProfile, p1_id)
                    p2 = session.get(PlayerProfile, p2_id)
                    if p1 and p2:
                        recent_activity.append({
                            "type": "new_friendship",
                            "title": "New Friendship",
                            "description": f"{p1.display_name} and {p2.display_name} connected as friends.",
                            "timestamp": f.created_at.isoformat(),
                            "actor_id": p1_id,
                            "actor_name": p1.display_name,
                        })

                # B. Recent Tournament Accolades among friends
                recent_tourns = session.execute(
                    select(Tournament, TournamentParticipant, PlayerProfile)
                    .join(TournamentParticipant, TournamentParticipant.tournament_id == Tournament.id)
                    .join(PlayerProfile, PlayerProfile.id == TournamentParticipant.player_id)
                    .where(
                        TournamentParticipant.player_id.in_(friend_ids),
                        Tournament.status == "completed",
                        TournamentParticipant.status.in_(["champion", "runner_up"]) | TournamentParticipant.final_placement.in_([1, 2]),
                    )
                    .order_by(desc(Tournament.completed_at))
                    .limit(5)
                ).all()

                for tourn, part, profile in recent_tourns:
                    if self._block_service.is_blocked(player_id, part.player_id, session=session):
                        continue
                    is_champ = part.final_placement == 1 or part.status == "champion"
                    title = "Tournament Champion" if is_champ else "Tournament Runner-Up"
                    recent_activity.append({
                        "type": "tournament_podium",
                        "title": title,
                        "description": f"{profile.display_name} earned {title} in {tourn.name}!",
                        "timestamp": (tourn.completed_at or tourn.updated_at).isoformat(),
                        "actor_id": profile.id,
                        "actor_name": profile.display_name,
                        "target_type": "tournament",
                        "target_id": tourn.id,
                    })

                # C. Recent League Season Podiums among friends
                recent_seasons = session.execute(
                    select(League, LeagueSeason, SeasonParticipant, PlayerProfile)
                    .join(LeagueSeason, LeagueSeason.league_id == League.id)
                    .join(SeasonParticipant, SeasonParticipant.season_id == LeagueSeason.id)
                    .join(PlayerProfile, PlayerProfile.id == SeasonParticipant.player_id)
                    .where(
                        SeasonParticipant.player_id.in_(friend_ids),
                        LeagueSeason.status == "completed",
                        SeasonParticipant.final_placement.in_([1, 2, 3]),
                    )
                    .order_by(desc(LeagueSeason.completed_at))
                    .limit(5)
                ).all()

                for league, season, part, profile in recent_seasons:
                    if self._block_service.is_blocked(player_id, part.player_id, session=session):
                        continue
                    place_str = "1st Place" if part.final_placement == 1 else "2nd Place" if part.final_placement == 2 else "3rd Place"
                    recent_activity.append({
                        "type": "league_podium",
                        "title": f"League Podium ({place_str})",
                        "description": f"{profile.display_name} finished {place_str} in {league.name} ({season.name})!",
                        "timestamp": (season.completed_at or season.updated_at).isoformat(),
                        "actor_id": profile.id,
                        "actor_name": profile.display_name,
                        "target_type": "league",
                        "target_id": league.id,
                    })

            # Sort activity by newest
            recent_activity.sort(key=lambda x: x["timestamp"], reverse=True)
            recent_activity = recent_activity[:10]

            # 7. Player's Own Recent Competition
            own_tournaments = session.scalars(
                select(TournamentParticipant)
                .where(TournamentParticipant.player_id == player_id)
                .order_by(desc(TournamentParticipant.id))
                .limit(3)
            ).all()

            own_seasons = session.scalars(
                select(SeasonParticipant)
                .where(SeasonParticipant.player_id == player_id)
                .order_by(desc(SeasonParticipant.id))
                .limit(3)
            ).all()

            # 8. Player's Own Rating Milestones
            ratings = session.scalars(
                select(PlayerRating).where(PlayerRating.player_id == player_id)
            ).all()
            rating_summary = {r.mode: r.rating for r in ratings}

        return {
            "friend_count": friend_count,
            "online_friends_count": online_friends,
            "pending_requests_count": pending_requests,
            "pending_invitations_count": pending_invitations,
            "pending_challenges_count": pending_challenges,
            "unread_messages_count": unread_dms,
            "recent_activity": recent_activity,
            "active_tournament_entries": len(own_tournaments),
            "active_season_entries": len(own_seasons),
            "ratings": rating_summary,
        }

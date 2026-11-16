"""Durable private messaging persistence service: conversations, direct messages, reactions, reports, and retention."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select, update

from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    DirectConversation,
    DirectMessage,
    DirectMessageReaction,
    Friendship,
    MessageReport,
    PlayerBlock,
    PlayerNotification,
    PlayerProfile,
    utc_now,
)
from quizverse.persistence.social import BlockService, FriendService

logger = logging.getLogger(__name__)

ALLOWED_REACTIONS: frozenset[str] = frozenset({"👍", "❤️", "😂", "🔥", "👏", "😮", "😢"})
ALLOWED_REPORT_REASONS: frozenset[str] = frozenset({"harassment", "spam", "abuse", "inappropriate", "other"})
MAX_MESSAGE_LENGTH: int = 2000
EDIT_WINDOW_SECONDS: float = 300.0  # 5 minutes
DEFAULT_RETENTION_DAYS: int = 30


class MessagingError(Exception):
    """Domain exception raised by messaging persistence operations."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class MessagingService:
    """Manages 1-on-1 conversations, persistent messages, reactions, reporting, and retention."""

    def __init__(
        self,
        database: Database,
        friend_service: FriendService,
        block_service: BlockService,
        *,
        clock: Callable[[], datetime] = utc_now,
        notification_service: Any = None,
    ) -> None:
        self._database = database
        self.friend_service = friend_service
        self.block_service = block_service
        self._clock = clock
        self.notification_service = notification_service

    def _canonical_pair(self, p1: str, p2: str) -> tuple[str, str]:
        return (p1, p2) if p1 < p2 else (p2, p1)

    def get_or_create_conversation(self, player_a_id: str, player_b_id: str) -> dict[str, Any]:
        """Retrieve existing or initiate a new canonical 1-on-1 conversation between accepted friends."""
        if player_a_id == player_b_id:
            raise MessagingError("cannot_message_self", "You cannot start a conversation with yourself.", 400)

        p1, p2 = self._canonical_pair(player_a_id, player_b_id)

        with self._database.sessions.begin() as session:
            # 1. Verify bidirectional blocking
            if self.block_service.is_blocked(player_a_id, player_b_id, session=session):
                raise MessagingError("blocked", "Communication is blocked between these players.", 403)

            # 2. Verify active accepted friendship
            if not self.friend_service.are_friends(player_a_id, player_b_id, session=session):
                raise MessagingError("not_friends", "You can only message accepted friends.", 403)

            # 3. Check for existing canonical conversation
            conv = session.scalar(
                select(DirectConversation).where(
                    DirectConversation.player_one_id == p1,
                    DirectConversation.player_two_id == p2,
                )
            )

            now = self._clock()
            if conv is None:
                conv = DirectConversation(
                    id=secrets.token_hex(16),
                    player_one_id=p1,
                    player_two_id=p2,
                    created_at=now,
                    updated_at=now,
                    player_one_unread_count=0,
                    player_two_unread_count=0,
                )
                session.add(conv)
                session.flush()

            # Load friend profile for recipient perspective
            friend_id = player_b_id
            friend = session.get(PlayerProfile, friend_id)

            return {
                "id": conv.id,
                "player_one_id": conv.player_one_id,
                "player_two_id": conv.player_two_id,
                "friend": {
                    "player_id": friend.id if friend else friend_id,
                    "display_name": friend.display_name if friend else "Player",
                },
                "unread_count": conv.player_one_unread_count if conv.player_one_id == player_a_id else conv.player_two_unread_count,
                "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
                "created_at": conv.created_at.isoformat(),
                "updated_at": conv.updated_at.isoformat(),
            }

    def list_conversations(self, player_id: str) -> list[dict[str, Any]]:
        """List active conversations for an authenticated player with friend details and unread counts."""
        with self._database.session() as session:
            # Query conversations where player participates
            query = (
                select(DirectConversation)
                .where(
                    or_(
                        DirectConversation.player_one_id == player_id,
                        DirectConversation.player_two_id == player_id,
                    )
                )
                .order_by(
                    DirectConversation.last_message_at.desc().nullslast(),
                    DirectConversation.updated_at.desc(),
                )
            )
            conversations = session.scalars(query).all()
            if not conversations:
                return []

            # Gather friend IDs
            friend_ids = [
                c.player_two_id if c.player_one_id == player_id else c.player_one_id
                for c in conversations
            ]
            profiles = {
                p.id: p
                for p in session.scalars(
                    select(PlayerProfile).where(PlayerProfile.id.in_(friend_ids))
                ).all()
            }

            # Gather last messages
            last_msg_ids = [c.last_message_id for c in conversations if c.last_message_id]
            last_msgs = {}
            if last_msg_ids:
                last_msgs = {
                    m.id: m
                    for m in session.scalars(
                        select(DirectMessage).where(DirectMessage.id.in_(last_msg_ids))
                    ).all()
                }

            # Check blocking to filter or mark
            blocked_ids = self.block_service.get_blocking_and_blocked_ids(player_id, session=session)

            results: list[dict[str, Any]] = []
            for c in conversations:
                f_id = c.player_two_id if c.player_one_id == player_id else c.player_one_id
                if f_id in blocked_ids:
                    continue  # suppress blocked conversations

                friend_profile = profiles.get(f_id)
                unread = c.player_one_unread_count if c.player_one_id == player_id else c.player_two_unread_count

                last_preview = None
                last_msg = last_msgs.get(c.last_message_id)
                if last_msg:
                    last_preview = "[Message deleted]" if last_msg.deleted_at else last_msg.content[:60]

                results.append({
                    "id": c.id,
                    "friend": {
                        "player_id": f_id,
                        "display_name": friend_profile.display_name if friend_profile else "Player",
                        "status": friend_profile.status if friend_profile else "active",
                    },
                    "unread_count": unread,
                    "last_message_preview": last_preview,
                    "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
                    "created_at": c.created_at.isoformat(),
                    "updated_at": c.updated_at.isoformat(),
                })

            return results

    def get_conversation(self, conversation_id: str, player_id: str) -> dict[str, Any]:
        """Retrieve details and participant metadata for a specific conversation."""
        with self._database.session() as session:
            conv = session.get(DirectConversation, conversation_id)
            if conv is None:
                raise MessagingError("conversation_not_found", "Conversation not found.", 404)

            if conv.player_one_id != player_id and conv.player_two_id != player_id:
                raise MessagingError("access_denied", "You are not a participant in this conversation.", 403)

            friend_id = conv.player_two_id if conv.player_one_id == player_id else conv.player_one_id
            if self.block_service.is_blocked(player_id, friend_id, session=session):
                raise MessagingError("blocked", "Communication is blocked between these players.", 403)

            friend = session.get(PlayerProfile, friend_id)
            unread = conv.player_one_unread_count if conv.player_one_id == player_id else conv.player_two_unread_count

            return {
                "id": conv.id,
                "friend": {
                    "player_id": friend_id,
                    "display_name": friend.display_name if friend else "Player",
                    "status": friend.status if friend else "active",
                },
                "unread_count": unread,
                "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
                "created_at": conv.created_at.isoformat(),
                "updated_at": conv.updated_at.isoformat(),
            }

    def list_messages(
        self,
        conversation_id: str,
        player_id: str,
        limit: int = 50,
        before_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch paginated message history for an authorized participant."""
        limit = max(1, min(limit, 100))

        with self._database.session() as session:
            conv = session.get(DirectConversation, conversation_id)
            if conv is None:
                raise MessagingError("conversation_not_found", "Conversation not found.", 404)

            if conv.player_one_id != player_id and conv.player_two_id != player_id:
                raise MessagingError("access_denied", "You are not a participant in this conversation.", 403)

            friend_id = conv.player_two_id if conv.player_one_id == player_id else conv.player_one_id
            if self.block_service.is_blocked(player_id, friend_id, session=session):
                raise MessagingError("blocked", "Communication is blocked between these players.", 403)

            query = select(DirectMessage).where(DirectMessage.conversation_id == conversation_id)

            if before_id:
                pivot = session.get(DirectMessage, before_id)
                if pivot and pivot.conversation_id == conversation_id:
                    query = query.where(DirectMessage.created_at < pivot.created_at)

            query = query.order_by(DirectMessage.created_at.desc()).limit(limit + 1)
            fetched = session.scalars(query).all()

            has_more = len(fetched) > limit
            page = fetched[:limit]
            # Return in chronological order
            page.reverse()

            # Fetch reactions for these messages
            msg_ids = [m.id for m in page]
            reactions_by_msg: dict[str, list[dict[str, Any]]] = {mid: [] for mid in msg_ids}
            if msg_ids:
                reaction_rows = session.scalars(
                    select(DirectMessageReaction).where(DirectMessageReaction.message_id.in_(msg_ids))
                ).all()
                for r in reaction_rows:
                    reactions_by_msg[r.message_id].append({
                        "id": r.id,
                        "player_id": r.player_id,
                        "reaction": r.reaction,
                        "created_at": r.created_at.isoformat(),
                    })

            messages_data: list[dict[str, Any]] = []
            for m in page:
                is_deleted = m.deleted_at is not None
                is_mine = m.sender_id == player_id

                status = "sent"
                if m.read_at is not None:
                    status = "read"
                elif m.delivered_at is not None:
                    status = "delivered"

                # Aggregate reactions
                rx_list = reactions_by_msg.get(m.id, [])
                rx_summary: dict[str, list[str]] = {}
                for rx in rx_list:
                    rx_summary.setdefault(rx["reaction"], []).append(rx["player_id"])

                messages_data.append({
                    "id": m.id,
                    "conversation_id": m.conversation_id,
                    "sender_id": m.sender_id,
                    "is_mine": is_mine,
                    "content": "[Message deleted]" if is_deleted else m.content,
                    "is_deleted": is_deleted,
                    "is_edited": m.edited_at is not None and not is_deleted,
                    "status": status,
                    "created_at": m.created_at.isoformat(),
                    "edited_at": m.edited_at.isoformat() if m.edited_at else None,
                    "delivered_at": m.delivered_at.isoformat() if m.delivered_at else None,
                    "read_at": m.read_at.isoformat() if m.read_at else None,
                    "reactions": rx_summary,
                })

            return {
                "messages": messages_data,
                "has_more": has_more,
                "oldest_id": messages_data[0]["id"] if messages_data else None,
            }

    def send_message(self, conversation_id: str, sender_id: str, content: str) -> dict[str, Any]:
        """Commit an authoritative direct message and update conversation metadata."""
        cleaned = content.strip()
        if not cleaned:
            raise MessagingError("empty_message", "Message content cannot be empty.", 400)
        if len(cleaned) > MAX_MESSAGE_LENGTH:
            raise MessagingError("message_too_long", f"Message length exceeds {MAX_MESSAGE_LENGTH} characters.", 400)

        with self._database.sessions.begin() as session:
            conv = session.get(DirectConversation, conversation_id)
            if conv is None:
                raise MessagingError("conversation_not_found", "Conversation not found.", 404)

            if conv.player_one_id != sender_id and conv.player_two_id != sender_id:
                raise MessagingError("access_denied", "You are not a participant in this conversation.", 403)

            recipient_id = conv.player_two_id if conv.player_one_id == sender_id else conv.player_one_id

            if self.block_service.is_blocked(sender_id, recipient_id, session=session):
                raise MessagingError("blocked", "Communication is blocked between these players.", 403)

            if not self.friend_service.are_friends(sender_id, recipient_id, session=session):
                raise MessagingError("not_friends", "You can only message accepted friends.", 403)

            now = self._clock()
            msg_id = secrets.token_hex(16)
            message = DirectMessage(
                id=msg_id,
                conversation_id=conversation_id,
                sender_id=sender_id,
                message_type="text",
                content=cleaned,
                created_at=now,
            )
            session.add(message)

            # Update conversation metadata and increment recipient's unread count
            conv.last_message_at = now
            conv.last_message_id = msg_id
            conv.updated_at = now
            if conv.player_one_id == sender_id:
                conv.player_two_unread_count += 1
            else:
                conv.player_one_unread_count += 1

            sender = session.get(PlayerProfile, sender_id)
            sender_name = sender.display_name if sender else "Player"

            result_msg = {
                "id": msg_id,
                "conversation_id": conversation_id,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "recipient_id": recipient_id,
                "content": cleaned,
                "status": "sent",
                "is_mine": True,
                "is_deleted": False,
                "is_edited": False,
                "created_at": now.isoformat(),
                "reactions": {},
            }

        if self.notification_service is not None:
            self.notification_service.create_notification(
                recipient_id,
                "direct_message_received",
                "New Direct Message",
                f"New message from {sender_name}.",
                actor_id=sender_id,
                target_type="conversation",
                target_id=conversation_id,
                deduplication_key=f"dm:{conversation_id}",
                metadata={"conversation_id": conversation_id},
            )

        return result_msg

    def edit_message(self, message_id: str, sender_id: str, new_content: str) -> dict[str, Any]:
        """Edit message content within the 5-minute ownership window."""
        cleaned = new_content.strip()
        if not cleaned:
            raise MessagingError("empty_message", "Message content cannot be empty.", 400)
        if len(cleaned) > MAX_MESSAGE_LENGTH:
            raise MessagingError("message_too_long", f"Message length exceeds {MAX_MESSAGE_LENGTH} characters.", 400)

        with self._database.sessions.begin() as session:
            message = session.get(DirectMessage, message_id)
            if message is None:
                raise MessagingError("message_not_found", "Message not found.", 404)

            if message.sender_id != sender_id:
                raise MessagingError("unauthorized", "You can only edit your own messages.", 403)

            if message.deleted_at is not None:
                raise MessagingError("cannot_edit_deleted", "Cannot edit a deleted message.", 400)

            now = self._clock()
            # Ensure time comparison handles timezone correctly
            created_at = message.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            now_utc = now if now.tzinfo is not None else now.replace(tzinfo=UTC)

            if (now_utc - created_at).total_seconds() > EDIT_WINDOW_SECONDS:
                raise MessagingError("edit_window_expired", "Messages can only be edited within 5 minutes.", 400)

            conv = session.get(DirectConversation, message.conversation_id)
            recipient_id = conv.player_two_id if conv.player_one_id == sender_id else conv.player_one_id
            if self.block_service.is_blocked(sender_id, recipient_id, session=session):
                raise MessagingError("blocked", "Communication is blocked between these players.", 403)

            message.content = cleaned
            message.edited_at = now

            return {
                "id": message.id,
                "conversation_id": message.conversation_id,
                "recipient_id": recipient_id,
                "content": cleaned,
                "edited_at": now.isoformat(),
            }

    def delete_message(self, message_id: str, sender_id: str) -> dict[str, Any]:
        """Soft-delete message content and wipe the payload."""
        with self._database.sessions.begin() as session:
            message = session.get(DirectMessage, message_id)
            if message is None:
                raise MessagingError("message_not_found", "Message not found.", 404)

            if message.sender_id != sender_id:
                raise MessagingError("unauthorized", "You can only delete your own messages.", 403)

            now = self._clock()
            message.deleted_at = now
            message.content = "[Message deleted]"

            conv = session.get(DirectConversation, message.conversation_id)
            recipient_id = conv.player_two_id if conv.player_one_id == sender_id else conv.player_one_id

            return {
                "id": message.id,
                "conversation_id": message.conversation_id,
                "recipient_id": recipient_id,
                "deleted": True,
                "deleted_at": now.isoformat(),
            }

    def mark_as_read(self, conversation_id: str, player_id: str) -> dict[str, Any]:
        """Acknowledge reading of incoming messages and clear unread counts."""
        with self._database.sessions.begin() as session:
            conv = session.get(DirectConversation, conversation_id)
            if conv is None:
                raise MessagingError("conversation_not_found", "Conversation not found.", 404)

            if conv.player_one_id != player_id and conv.player_two_id != player_id:
                raise MessagingError("access_denied", "You are not a participant in this conversation.", 403)

            now = self._clock()
            other_player_id = conv.player_two_id if conv.player_one_id == player_id else conv.player_one_id

            # Reset this player's unread count
            if conv.player_one_id == player_id:
                conv.player_one_unread_count = 0
            else:
                conv.player_two_unread_count = 0

            # Find unread messages from other player
            unread_msgs = session.scalars(
                select(DirectMessage).where(
                    DirectMessage.conversation_id == conversation_id,
                    DirectMessage.sender_id == other_player_id,
                    DirectMessage.read_at.is_(None),
                )
            ).all()

            marked_ids = []
            for m in unread_msgs:
                m.read_at = now
                if m.delivered_at is None:
                    m.delivered_at = now
                marked_ids.append(m.id)

            # Clear or mark read any notification for this conversation
            session.execute(
                update(PlayerNotification)
                .where(
                    PlayerNotification.recipient_id == player_id,
                    PlayerNotification.deduplication_key == f"dm:{conversation_id}",
                    PlayerNotification.is_read.is_(False),
                )
                .values(is_read=True, read_at=now)
            )

        if self.notification_service is not None and self.notification_service.presence_service is not None:
            count = self.notification_service.get_unread_count(player_id)
            ps = self.notification_service.presence_service
            if hasattr(ps, "safe_dispatch"):
                ps.safe_dispatch(ps.send_notification_unread_count(player_id, count))
            else:
                try:
                    import asyncio
                    loop = asyncio.get_running_loop()
                    loop.create_task(ps.send_notification_unread_count(player_id, count))
                except RuntimeError:
                    pass

        return {
            "conversation_id": conversation_id,
            "player_id": player_id,
            "other_player_id": other_player_id,
            "read_at": now.isoformat(),
            "marked_message_ids": marked_ids,
        }

    def mark_delivered(self, message_id: str, recipient_id: str) -> bool:
        """Record real-time delivery receipt when delivered to an active socket."""
        with self._database.sessions.begin() as session:
            message = session.get(DirectMessage, message_id)
            if message is None or message.delivered_at is not None or message.read_at is not None:
                return False

            conv = session.get(DirectConversation, message.conversation_id)
            expected_recipient = conv.player_two_id if conv.player_one_id == message.sender_id else conv.player_one_id
            if expected_recipient != recipient_id:
                return False

            message.delivered_at = self._clock()
            return True

    def add_reaction(self, message_id: str, player_id: str, reaction: str) -> dict[str, Any]:
        """Add an emoji reaction to a message from an authorized participant."""
        if reaction not in ALLOWED_REACTIONS:
            raise MessagingError(
                "invalid_reaction",
                f"Reaction must be one of: {', '.join(sorted(ALLOWED_REACTIONS))}",
                400,
            )

        with self._database.sessions.begin() as session:
            message = session.get(DirectMessage, message_id)
            if message is None:
                raise MessagingError("message_not_found", "Message not found.", 404)

            conv = session.get(DirectConversation, message.conversation_id)
            if conv.player_one_id != player_id and conv.player_two_id != player_id:
                raise MessagingError("access_denied", "You are not a participant in this conversation.", 403)

            other_id = conv.player_two_id if conv.player_one_id == player_id else conv.player_one_id
            if self.block_service.is_blocked(player_id, other_id, session=session):
                raise MessagingError("blocked", "Communication is blocked between these players.", 403)

            # Check if reaction already exists
            existing = session.scalar(
                select(DirectMessageReaction).where(
                    DirectMessageReaction.message_id == message_id,
                    DirectMessageReaction.player_id == player_id,
                    DirectMessageReaction.reaction == reaction,
                )
            )
            if existing is None:
                now = self._clock()
                rx = DirectMessageReaction(
                    id=secrets.token_hex(16),
                    message_id=message_id,
                    player_id=player_id,
                    reaction=reaction,
                    created_at=now,
                )
                session.add(rx)
                session.flush()

            # Load all reactions for summary
            reactions = session.scalars(
                select(DirectMessageReaction).where(DirectMessageReaction.message_id == message_id)
            ).all()
            rx_summary: dict[str, list[str]] = {}
            for r in reactions:
                rx_summary.setdefault(r.reaction, []).append(r.player_id)

            return {
                "message_id": message_id,
                "conversation_id": conv.id,
                "player_id": player_id,
                "other_player_id": other_id,
                "reaction": reaction,
                "reactions": rx_summary,
            }

    def remove_reaction(self, message_id: str, player_id: str, reaction: str) -> dict[str, Any]:
        """Remove an emoji reaction previously added by the player."""
        if reaction not in ALLOWED_REACTIONS:
            raise MessagingError("invalid_reaction", f"Reaction must be one of: {', '.join(sorted(ALLOWED_REACTIONS))}", 400)

        with self._database.sessions.begin() as session:
            message = session.get(DirectMessage, message_id)
            if message is None:
                raise MessagingError("message_not_found", "Message not found.", 404)

            conv = session.get(DirectConversation, message.conversation_id)
            if conv.player_one_id != player_id and conv.player_two_id != player_id:
                raise MessagingError("access_denied", "You are not a participant in this conversation.", 403)

            other_id = conv.player_two_id if conv.player_one_id == player_id else conv.player_one_id

            session.execute(
                delete(DirectMessageReaction).where(
                    DirectMessageReaction.message_id == message_id,
                    DirectMessageReaction.player_id == player_id,
                    DirectMessageReaction.reaction == reaction,
                )
            )
            session.flush()

            reactions = session.scalars(
                select(DirectMessageReaction).where(DirectMessageReaction.message_id == message_id)
            ).all()
            rx_summary: dict[str, list[str]] = {}
            for r in reactions:
                rx_summary.setdefault(r.reaction, []).append(r.player_id)

            return {
                "message_id": message_id,
                "conversation_id": conv.id,
                "player_id": player_id,
                "other_player_id": other_id,
                "reaction": reaction,
                "reactions": rx_summary,
            }

    def report_message(
        self,
        message_id: str,
        reporter_id: str,
        reason: str,
        details: str | None = None,
    ) -> dict[str, Any]:
        """Submit a safety report against an inappropriate direct message."""
        if reason not in ALLOWED_REPORT_REASONS:
            raise MessagingError("invalid_report_reason", f"Reason must be one of: {', '.join(sorted(ALLOWED_REPORT_REASONS))}", 400)

        with self._database.sessions.begin() as session:
            message = session.get(DirectMessage, message_id)
            if message is None:
                raise MessagingError("message_not_found", "Message not found.", 404)

            conv = session.get(DirectConversation, message.conversation_id)
            if conv.player_one_id != reporter_id and conv.player_two_id != reporter_id:
                raise MessagingError("access_denied", "You are not a participant in this conversation.", 403)

            if message.sender_id == reporter_id:
                raise MessagingError("cannot_report_self", "You cannot report your own message.", 400)

            # Prevent duplicate report by same player for same message
            existing = session.scalar(
                select(MessageReport).where(
                    MessageReport.reporter_id == reporter_id,
                    MessageReport.message_id == message_id,
                )
            )
            if existing is not None:
                raise MessagingError("already_reported", "You have already reported this message.", 400)

            now = self._clock()
            report = MessageReport(
                id=secrets.token_hex(16),
                reporter_id=reporter_id,
                message_id=message_id,
                reason=reason,
                details=details.strip() if details else None,
                status="pending",
                created_at=now,
            )
            session.add(report)

            return {
                "id": report.id,
                "message_id": message_id,
                "reporter_id": reporter_id,
                "reason": reason,
                "status": "pending",
                "created_at": now.isoformat(),
            }

    def cleanup_expired_messages(self, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
        """Purge direct messages older than the retention threshold."""
        cutoff = self._clock() - timedelta(days=retention_days)
        with self._database.sessions.begin() as session:
            result = session.execute(
                delete(DirectMessage).where(DirectMessage.created_at < cutoff)
            )
            count = result.rowcount if hasattr(result, "rowcount") else 0
            logger.info("Purged %d messages older than %s (%d days)", count, cutoff, retention_days)
            return count

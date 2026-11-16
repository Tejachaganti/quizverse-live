"""Authoritative service for room invitations, secure admission, and direct casual rematches."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, or_, select, update

from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    CompetitiveMatch,
    MatchParticipant,
    PlayerProfile,
    RematchChallenge,
    RoomInvitation,
    utc_now,
)
from quizverse.persistence.social import BlockService, FriendService
from quizverse.presence import PresenceService

logger = logging.getLogger(__name__)

INVITATION_TTL_SECONDS = 60
REMATCH_TTL_SECONDS = 60
MAX_PENDING_OUTGOING = 3

CASUAL_REMATCH_MODES = {
    "classic",
    "speed_challenge",
    "team_battle",
    "elimination",
    "quiz_royale",
    "power_play",
    "bluff_master",
    "escape_room",
}


def _as_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.min.replace(tzinfo=UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class InvitationError(Exception):
    """Domain exception for room invitation and rematch operations."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class InvitationService:
    """Manages the lifecycle of friend-to-friend game room invitations and admission tickets."""

    def __init__(
        self,
        database: Database,
        friend_service: FriendService,
        block_service: BlockService,
        presence_service: PresenceService | None = None,
        notification_service: Any = None,
    ) -> None:
        self._database = database
        self._friend_service = friend_service
        self._block_service = block_service
        self.presence_service = presence_service
        self.notification_service = notification_service

    async def create_invitation(
        self,
        inviter_id: str,
        recipient_id: str | None = None,
        room_code: str = "",
        engine: Any = None,
        *,
        invitee_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new room invitation after verifying friendship, block status, and room state."""
        target_id = recipient_id or invitee_id
        if not target_id:
            raise InvitationError("missing_invitee", "Invitee ID is required.", 400)
        recipient_id = target_id
        if inviter_id == recipient_id:
            raise InvitationError("cannot_invite_self", "You cannot invite yourself.", 400)

        clean_code = (room_code or "").strip().upper()
        if not clean_code:
            raise InvitationError("invalid_room_code", "A valid room code is required.", 400)

        # 1. Authoritative room state check from GameEngine
        room_meta = await engine.get_room_metadata(clean_code)
        if room_meta is None:
            raise InvitationError("room_not_found", "The specified arena was not found or has closed.", 404)

        if not room_meta["is_waiting"]:
            raise InvitationError("room_not_waiting", "Invitations can only be sent while the arena is in the waiting lobby.", 400)

        if room_meta["is_full"]:
            raise InvitationError("room_full", "The arena is full and cannot accept more players.", 400)

        if inviter_id not in room_meta["member_profile_ids"] and room_meta["host_profile_id"] != inviter_id:
            raise InvitationError("not_room_member", "You must be a member or host of the arena to send invitations.", 403)

        if recipient_id in room_meta["member_profile_ids"]:
            raise InvitationError("already_in_room", "This player is already in the arena.", 400)

        now = utc_now()
        with self._database.sessions.begin() as session:
            # 2. Recipient profile validity
            recipient = session.get(PlayerProfile, recipient_id)
            if recipient is None or recipient.status != "active":
                raise InvitationError("recipient_not_found", "The recipient player profile was not found or is inactive.", 404)

            inviter = session.get(PlayerProfile, inviter_id)
            inviter_name = inviter.display_name if inviter else "Player"

            # 3. Block check
            if self._block_service.is_blocked(inviter_id, recipient_id, session=session):
                raise InvitationError("blocked_player", "Cannot invite this player due to privacy restrictions.", 403)

            # 4. Friendship check
            if not self._friend_service.are_friends(inviter_id, recipient_id, session=session):
                raise InvitationError("not_friends", "You can only invite accepted friends.", 403)

            # 5. Outgoing pending count limit
            pending_count = session.scalar(
                select(RoomInvitation)
                .where(
                    RoomInvitation.inviter_id == inviter_id,
                    RoomInvitation.status == "pending",
                    RoomInvitation.expires_at > now,
                )
            )
            total_pending = len(
                session.scalars(
                    select(RoomInvitation.id).where(
                        RoomInvitation.inviter_id == inviter_id,
                        RoomInvitation.status == "pending",
                        RoomInvitation.expires_at > now,
                    )
                ).all()
            )
            if total_pending >= MAX_PENDING_OUTGOING:
                raise InvitationError(
                    "max_pending_invitations",
                    f"You have reached the maximum number of pending invitations ({MAX_PENDING_OUTGOING}).",
                    429,
                )

            # 6. Duplicate pending invitation check for this room and recipient
            existing = session.scalar(
                select(RoomInvitation).where(
                    RoomInvitation.invitee_id == recipient_id,
                    RoomInvitation.room_code == clean_code,
                    RoomInvitation.status == "pending",
                    RoomInvitation.expires_at > now,
                )
            )
            if existing is not None:
                raise InvitationError("duplicate_invitation", "A pending invitation for this arena has already been sent to this player.", 409)

            expires_at = now + timedelta(seconds=INVITATION_TTL_SECONDS)
            invitation = RoomInvitation(
                id=secrets.token_hex(16),
                inviter_id=inviter_id,
                invitee_id=recipient_id,
                room_code=clean_code,
                status="pending",
                created_at=now,
                expires_at=expires_at,
            )
            session.add(invitation)

        notification_payload = {
            "invitation_id": invitation.id,
            "inviter_id": inviter_id,
            "inviter_name": inviter_name,
            "room_code": clean_code,
            "mode": room_meta["mode"],
            "expires_at": expires_at.isoformat(),
        }

        # 7. Deliver real-time notification if recipient is online
        if self.presence_service is not None:
            asyncio.create_task(
                self.presence_service.send_invitation_notification(recipient_id, notification_payload)
            )

        if self.notification_service is not None:
            self.notification_service.create_notification(
                recipient_id,
                "room_invitation_received",
                "Game Room Invitation",
                f"{inviter_name} invited you to play {room_meta['mode'].title()} (Room {clean_code}).",
                actor_id=inviter_id,
                target_type="room_invitation",
                target_id=invitation.id,
                metadata={"room_code": clean_code, "mode": room_meta["mode"]},
                deduplication_key=f"room_inv:{invitation.id}",
                source_event_id=invitation.id,
                expires_in_days=30,
            )

        return {
            "invitation_id": invitation.id,
            "inviter_id": inviter_id,
            "invitee_id": recipient_id,
            "room_code": clean_code,
            "status": "pending",
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

    def list_incoming_invitations(self, player_id: str) -> list[dict[str, Any]]:
        """Return pending, unexpired incoming invitations for the player."""
        now = utc_now()
        with self._database.sessions.begin() as session:
            # Cleanly mark expired ones
            session.execute(
                update(RoomInvitation)
                .where(
                    RoomInvitation.invitee_id == player_id,
                    RoomInvitation.status == "pending",
                    RoomInvitation.expires_at <= now,
                )
                .values(status="expired")
            )

            blocked_ids = self._block_service.get_blocking_and_blocked_ids(player_id, session=session)

            rows = session.execute(
                select(RoomInvitation, PlayerProfile)
                .join(PlayerProfile, PlayerProfile.id == RoomInvitation.inviter_id)
                .where(
                    RoomInvitation.invitee_id == player_id,
                    RoomInvitation.status == "pending",
                    RoomInvitation.expires_at > now,
                )
                .order_by(RoomInvitation.created_at.desc())
            ).all()

            results: list[dict[str, Any]] = []
            for inv, inviter in rows:
                if inv.inviter_id in blocked_ids:
                    continue
                results.append({
                    "invitation_id": inv.id,
                    "inviter_id": inv.inviter_id,
                    "inviter_name": inviter.display_name,
                    "room_code": inv.room_code,
                    "status": inv.status,
                    "created_at": inv.created_at.isoformat(),
                    "expires_at": inv.expires_at.isoformat(),
                    "seconds_remaining": max(0, int((_as_utc(inv.expires_at) - now).total_seconds())),
                })
            return results

    def list_outgoing_invitations(self, player_id: str) -> list[dict[str, Any]]:
        """Return pending, unexpired outgoing invitations sent by the player."""
        now = utc_now()
        with self._database.sessions.begin() as session:
            session.execute(
                update(RoomInvitation)
                .where(
                    RoomInvitation.inviter_id == player_id,
                    RoomInvitation.status == "pending",
                    RoomInvitation.expires_at <= now,
                )
                .values(status="expired")
            )

            rows = session.execute(
                select(RoomInvitation, PlayerProfile)
                .join(PlayerProfile, PlayerProfile.id == RoomInvitation.invitee_id)
                .where(
                    RoomInvitation.inviter_id == player_id,
                    RoomInvitation.status == "pending",
                    RoomInvitation.expires_at > now,
                )
                .order_by(RoomInvitation.created_at.desc())
            ).all()

            results: list[dict[str, Any]] = []
            for inv, invitee in rows:
                results.append({
                    "invitation_id": inv.id,
                    "invitee_id": inv.invitee_id,
                    "invitee_name": invitee.display_name,
                    "room_code": inv.room_code,
                    "status": inv.status,
                    "created_at": inv.created_at.isoformat(),
                    "expires_at": inv.expires_at.isoformat(),
                    "seconds_remaining": max(0, int((_as_utc(inv.expires_at) - now).total_seconds())),
                })
            return results

    async def accept_invitation(
        self,
        invitation_id: str,
        invitee_id: str,
        engine: Any,
    ) -> dict[str, Any]:
        """Accept a pending invitation and issue a room-bound single-use admission ticket."""
        now = utc_now()
        raw_ticket = secrets.token_urlsafe(32)
        ticket_hash = _token_hash(raw_ticket)
        ticket_expires = now + timedelta(seconds=60)

        with self._database.sessions.begin() as session:
            inv = session.get(RoomInvitation, invitation_id)
            if inv is None or inv.invitee_id != invitee_id:
                raise InvitationError("invitation_not_found", "The invitation was not found.", 404)

            if inv.status != "pending":
                raise InvitationError("invalid_invitation_status", f"Cannot accept an invitation with status '{inv.status}'.", 400)

            if _as_utc(inv.expires_at) <= now:
                inv.status = "expired"
                raise InvitationError("invitation_expired", "This invitation has expired.", 400)

            # Block check
            if self._block_service.is_blocked(inv.inviter_id, invitee_id, session=session):
                raise InvitationError("blocked_player", "Cannot accept invitation due to player privacy restrictions.", 403)

            # Friendship check
            if not self._friend_service.are_friends(inv.inviter_id, invitee_id, session=session):
                raise InvitationError("not_friends", "Cannot accept invitation from a player who is not an accepted friend.", 403)

            room_code = inv.room_code

            # Room state validation from engine
            room_meta = await engine.get_room_metadata(room_code)
            if room_meta is None:
                raise InvitationError("room_not_found", "The game arena has closed or expired.", 404)

            if not room_meta["is_waiting"]:
                raise InvitationError("game_started", "The game in this arena has already started.", 400)

            if room_meta["is_full"]:
                raise InvitationError("room_full", "The arena is full and cannot accept more players.", 400)

            # Update invitation status transactionally
            inv.status = "accepted"
            inv.responded_at = now
            inv.admission_ticket_hash = ticket_hash
            inv.ticket_expires_at = ticket_expires
            inv.ticket_used_at = None
            inviter_id = inv.inviter_id

        if self.notification_service is not None:
            self.notification_service.create_notification(
                inviter_id,
                "room_invitation_accepted",
                "Room Invitation Accepted",
                f"Your game room invitation to Room {room_code} was accepted.",
                actor_id=invitee_id,
                target_type="room_invitation",
                target_id=invitation_id,
                metadata={"room_code": room_code},
                deduplication_key=f"room_inv_acc:{invitation_id}",
                source_event_id=invitation_id,
            )

        return {
            "invitation_id": invitation_id,
            "room_code": room_code,
            "admission_ticket": raw_ticket,
            "expires_in_seconds": 60,
        }

    async def admit_to_room(
        self,
        invitation_id: str,
        invitee_id: str,
        admission_ticket: str,
        player_name: str,
        engine: Any,
    ) -> dict[str, Any]:
        """Atomically validate ticket, mark used, and join the room authoritatively."""
        now = utc_now()
        digest = _token_hash(admission_ticket)

        with self._database.sessions.begin() as session:
            inv = session.get(RoomInvitation, invitation_id)
            if inv is None or inv.invitee_id != invitee_id:
                raise InvitationError("invitation_not_found", "The invitation was not found.", 404)

            if inv.status != "accepted":
                raise InvitationError("invitation_not_accepted", "The invitation has not been accepted.", 400)

            if inv.admission_ticket_hash != digest:
                raise InvitationError("invalid_admission_ticket", "The admission ticket is invalid.", 401)

            if inv.ticket_used_at is not None:
                raise InvitationError("ticket_already_used", "The admission ticket has already been used.", 401)

            if _as_utc(inv.ticket_expires_at) <= now:
                raise InvitationError("ticket_expired", "The admission ticket has expired.", 401)

            # Consume atomically
            inv.ticket_used_at = now
            room_code = inv.room_code

        # Join room in GameEngine
        joined = await engine.join_room(
            room_code,
            player_name,
            profile_id=invitee_id,
            is_invitation_admission=True,
        )
        return joined

    def validate_and_consume_ticket(
        self,
        room_code: str,
        profile_id: str,
        raw_ticket: str,
    ) -> bool:
        """Validate and consume ticket during direct WebSocket join."""
        if not raw_ticket:
            return False
        now = utc_now()
        digest = _token_hash(raw_ticket)

        with self._database.sessions.begin() as session:
            inv = session.scalar(
                select(RoomInvitation).where(
                    RoomInvitation.admission_ticket_hash == digest,
                    RoomInvitation.room_code == room_code.upper(),
                    RoomInvitation.invitee_id == profile_id,
                    RoomInvitation.ticket_used_at.is_(None),
                )
            )
            if inv is None:
                return False
            if _as_utc(inv.ticket_expires_at) <= now:
                return False
            inv.ticket_used_at = now
            return True

    def decline_invitation(self, invitation_id: str, invitee_id: str) -> dict[str, Any]:
        """Decline a pending invitation."""
        now = utc_now()
        with self._database.sessions.begin() as session:
            inv = session.get(RoomInvitation, invitation_id)
            if inv is None or inv.invitee_id != invitee_id:
                raise InvitationError("invitation_not_found", "The invitation was not found.", 404)

            if inv.status != "pending":
                raise InvitationError("invalid_invitation_status", f"Cannot decline an invitation with status '{inv.status}'.", 400)

            inv.status = "declined"
            inv.responded_at = now
            inviter_id = inv.inviter_id
            room_code = inv.room_code

        if self.notification_service is not None:
            self.notification_service.create_notification(
                inviter_id,
                "room_invitation_declined",
                "Room Invitation Declined",
                f"Your game room invitation to Room {room_code} was declined.",
                actor_id=invitee_id,
                target_type="room_invitation",
                target_id=invitation_id,
                deduplication_key=f"room_inv_dec:{invitation_id}",
                source_event_id=invitation_id,
            )

        return {"invitation_id": invitation_id, "status": "declined"}

    def cancel_invitation(self, invitation_id: str, inviter_id: str) -> dict[str, Any]:
        """Cancel a pending outgoing invitation."""
        now = utc_now()
        with self._database.sessions.begin() as session:
            inv = session.get(RoomInvitation, invitation_id)
            if inv is None or inv.inviter_id != inviter_id:
                raise InvitationError("invitation_not_found", "The invitation was not found.", 404)

            if inv.status != "pending":
                raise InvitationError("invalid_invitation_status", f"Cannot cancel an invitation with status '{inv.status}'.", 400)

            inv.status = "cancelled"
            inv.responded_at = now

        return {"invitation_id": invitation_id, "status": "cancelled"}

    def cancel_pending_between_players(self, p1: str, p2: str) -> None:
        """Cancel all pending invitations between two players when friendship is severed or blocked."""
        now = utc_now()
        with self._database.sessions.begin() as session:
            session.execute(
                update(RoomInvitation)
                .where(
                    or_(
                        (RoomInvitation.inviter_id == p1) & (RoomInvitation.invitee_id == p2),
                        (RoomInvitation.inviter_id == p2) & (RoomInvitation.invitee_id == p1),
                    ),
                    RoomInvitation.status == "pending",
                )
                .values(status="cancelled", responded_at=now)
            )


class RematchService:
    """Manages casual direct rematch challenges derived from completed historical matches."""

    def __init__(
        self,
        database: Database,
        friend_service: FriendService,
        block_service: BlockService,
        presence_service: PresenceService | None = None,
        notification_service: Any = None,
    ) -> None:
        self._database = database
        self._friend_service = friend_service
        self._block_service = block_service
        self.presence_service = presence_service
        self.notification_service = notification_service

    async def create_challenge(
        self,
        challenger_id: str,
        opponent_id: str,
        original_match_id: str,
    ) -> dict[str, Any]:
        """Create a rematch challenge for a completed historical match."""
        if challenger_id == opponent_id:
            raise InvitationError("cannot_challenge_self", "You cannot challenge yourself to a rematch.", 400)

        now = utc_now()
        with self._database.sessions.begin() as session:
            # 1. Opponent profile check
            opponent = session.get(PlayerProfile, opponent_id)
            if opponent is None or opponent.status != "active":
                raise InvitationError("opponent_not_found", "The opponent player profile was not found or is inactive.", 404)

            challenger = session.get(PlayerProfile, challenger_id)
            challenger_name = challenger.display_name if challenger else "Player"

            # 2. Block check
            if self._block_service.is_blocked(challenger_id, opponent_id, session=session):
                raise InvitationError("blocked_player", "Cannot challenge this player due to privacy restrictions.", 403)

            # 3. Match historical existence and eligibility
            match = session.get(CompetitiveMatch, original_match_id)
            if match is None:
                raise InvitationError("match_not_found", "The historical match was not found.", 404)

            if match.status != "completed":
                raise InvitationError("match_not_completed", "Only completed matches can be rematched.", 400)

            # Exclude official tournament matches
            ctx = match.result_context or {}
            if ctx.get("tournament_id") or ctx.get("tournament_match_id"):
                raise InvitationError("tournament_rematch_ineligible", "Official tournament matches cannot be directly rematched.", 400)

            # Exclude official league fixtures
            if ctx.get("league_id") or ctx.get("league_fixture_id"):
                raise InvitationError("league_rematch_ineligible", "Official league fixtures cannot be directly rematched.", 400)

            if match.mode == "solo_practice":
                raise InvitationError("mode_ineligible", "Solo practice matches cannot be rematched.", 400)

            if match.mode not in CASUAL_REMATCH_MODES:
                raise InvitationError("mode_ineligible", f"Mode '{match.mode}' does not support casual rematches.", 400)

            # 4. Verify participant membership
            participants = session.scalars(
                select(MatchParticipant.player_id).where(
                    MatchParticipant.match_id == original_match_id,
                )
            ).all()
            participant_set = {p for p in participants if p is not None}
            if challenger_id not in participant_set or opponent_id not in participant_set:
                raise InvitationError(
                    "unrelated_match_participant",
                    "Both the challenger and opponent must have participated in the original match.",
                    400,
                )

            # 5. Outgoing pending count limit
            total_pending = len(
                session.scalars(
                    select(RematchChallenge.id).where(
                        RematchChallenge.challenger_id == challenger_id,
                        RematchChallenge.status == "pending",
                        RematchChallenge.expires_at > now,
                    )
                ).all()
            )
            if total_pending >= MAX_PENDING_OUTGOING:
                raise InvitationError(
                    "max_pending_challenges",
                    f"You have reached the maximum number of pending rematch challenges ({MAX_PENDING_OUTGOING}).",
                    429,
                )

            # 6. Duplicate pending challenge check
            existing = session.scalar(
                select(RematchChallenge).where(
                    RematchChallenge.original_match_id == original_match_id,
                    RematchChallenge.challenger_id == challenger_id,
                    RematchChallenge.opponent_id == opponent_id,
                    RematchChallenge.status == "pending",
                    RematchChallenge.expires_at > now,
                )
            )
            if existing is not None:
                raise InvitationError("duplicate_challenge", "A pending rematch challenge has already been sent for this match.", 409)

            expires_at = now + timedelta(seconds=REMATCH_TTL_SECONDS)
            challenge = RematchChallenge(
                id=secrets.token_hex(16),
                original_match_id=original_match_id,
                challenger_id=challenger_id,
                opponent_id=opponent_id,
                mode=match.mode,
                status="pending",
                created_at=now,
                expires_at=expires_at,
            )
            session.add(challenge)

        notification_payload = {
            "challenge_id": challenge.id,
            "challenger_id": challenger_id,
            "challenger_name": challenger_name,
            "original_match_id": original_match_id,
            "mode": match.mode,
            "expires_at": expires_at.isoformat(),
        }

        # 7. Real-time notification
        if self.presence_service is not None:
            asyncio.create_task(
                self.presence_service.send_rematch_notification(opponent_id, notification_payload)
            )

        if self.notification_service is not None:
            self.notification_service.create_notification(
                opponent_id,
                "rematch_challenge_received",
                "Rematch Challenge",
                f"{challenger_name} challenged you to a rematch in {match.mode.title()}!",
                actor_id=challenger_id,
                target_type="rematch_challenge",
                target_id=challenge.id,
                metadata={"mode": match.mode, "original_match_id": original_match_id},
                deduplication_key=f"rematch:{challenge.id}",
                source_event_id=challenge.id,
                expires_in_days=30,
            )

        return {
            "challenge_id": challenge.id,
            "challenger_id": challenger_id,
            "opponent_id": opponent_id,
            "original_match_id": original_match_id,
            "mode": match.mode,
            "status": "pending",
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

    def list_incoming_challenges(self, player_id: str) -> list[dict[str, Any]]:
        """Return pending incoming rematch challenges."""
        now = utc_now()
        with self._database.sessions.begin() as session:
            session.execute(
                update(RematchChallenge)
                .where(
                    RematchChallenge.opponent_id == player_id,
                    RematchChallenge.status == "pending",
                    RematchChallenge.expires_at <= now,
                )
                .values(status="expired")
            )

            blocked_ids = self._block_service.get_blocking_and_blocked_ids(player_id, session=session)

            rows = session.execute(
                select(RematchChallenge, PlayerProfile)
                .join(PlayerProfile, PlayerProfile.id == RematchChallenge.challenger_id)
                .where(
                    RematchChallenge.opponent_id == player_id,
                    RematchChallenge.status == "pending",
                    RematchChallenge.expires_at > now,
                )
                .order_by(RematchChallenge.created_at.desc())
            ).all()

            results: list[dict[str, Any]] = []
            for chal, challenger in rows:
                if chal.challenger_id in blocked_ids:
                    continue
                results.append({
                    "challenge_id": chal.id,
                    "challenger_id": chal.challenger_id,
                    "challenger_name": challenger.display_name,
                    "original_match_id": chal.original_match_id,
                    "mode": chal.mode,
                    "status": chal.status,
                    "created_at": chal.created_at.isoformat(),
                    "expires_at": chal.expires_at.isoformat(),
                    "seconds_remaining": max(0, int((_as_utc(chal.expires_at) - now).total_seconds())),
                })
            return results

    def list_outgoing_challenges(self, player_id: str) -> list[dict[str, Any]]:
        """Return pending outgoing rematch challenges."""
        now = utc_now()
        with self._database.sessions.begin() as session:
            session.execute(
                update(RematchChallenge)
                .where(
                    RematchChallenge.challenger_id == player_id,
                    RematchChallenge.status == "pending",
                    RematchChallenge.expires_at <= now,
                )
                .values(status="expired")
            )

            rows = session.execute(
                select(RematchChallenge, PlayerProfile)
                .join(PlayerProfile, PlayerProfile.id == RematchChallenge.opponent_id)
                .where(
                    RematchChallenge.challenger_id == player_id,
                    RematchChallenge.status == "pending",
                    RematchChallenge.expires_at > now,
                )
                .order_by(RematchChallenge.created_at.desc())
            ).all()

            results: list[dict[str, Any]] = []
            for chal, opponent in rows:
                results.append({
                    "challenge_id": chal.id,
                    "opponent_id": chal.opponent_id,
                    "opponent_name": opponent.display_name,
                    "original_match_id": chal.original_match_id,
                    "mode": chal.mode,
                    "status": chal.status,
                    "created_at": chal.created_at.isoformat(),
                    "expires_at": chal.expires_at.isoformat(),
                    "seconds_remaining": max(0, int((_as_utc(chal.expires_at) - now).total_seconds())),
                })
            return results

    async def accept_challenge(
        self,
        challenge_id: str,
        opponent_id: str,
        engine: Any,
    ) -> dict[str, Any]:
        """Accept challenge, create fresh casual room, and authorize both players."""
        now = utc_now()
        with self._database.sessions.begin() as session:
            chal = session.get(RematchChallenge, challenge_id)
            if chal is None or chal.opponent_id != opponent_id:
                raise InvitationError("challenge_not_found", "The rematch challenge was not found.", 404)

            if chal.status != "pending":
                raise InvitationError("invalid_challenge_status", f"Cannot accept challenge with status '{chal.status}'.", 400)

            if _as_utc(chal.expires_at) <= now:
                chal.status = "expired"
                raise InvitationError("challenge_expired", "This rematch challenge has expired.", 400)

            # Block check
            if self._block_service.is_blocked(chal.challenger_id, opponent_id, session=session):
                raise InvitationError("blocked_player", "Cannot accept rematch due to player privacy restrictions.", 403)

            challenger_id = chal.challenger_id
            mode = chal.mode
            opponent_prof = session.get(PlayerProfile, opponent_id)
            opponent_name = opponent_prof.display_name if opponent_prof else "Player"

            # Create fresh casual room in GameEngine
            # Strictly clean selection with zero tournament/league linkage
            clean_selection = {
                "is_rematch": True,
                "access_policy": "invite_only",
                "rematch_challenge_id": chal.id,
                "original_match_id": chal.original_match_id,
            }
            joined = await engine.create_room(
                opponent_name,
                mode=mode,
                selection=clean_selection,
                profile_id=opponent_id,
            )
            new_room_code = joined["state"]["code"]

            # Authorize both players to enter this private rematch room
            async with engine._lock:
                room = engine._rooms.get(new_room_code)
                if room is not None:
                    room.authorized_profile_ids.add(challenger_id)
                    room.authorized_profile_ids.add(opponent_id)

            chal.status = "accepted"
            chal.responded_at = now
            chal.resulting_room_code = new_room_code

        # Notify challenger in real-time
        if self.presence_service is not None:
            asyncio.create_task(
                self.presence_service.send_rematch_accepted_notification(
                    challenger_id,
                    {
                        "challenge_id": challenge_id,
                        "room_code": new_room_code,
                        "mode": mode,
                        "opponent_id": opponent_id,
                        "opponent_name": opponent_name,
                    },
                )
            )

        if self.notification_service is not None:
            self.notification_service.create_notification(
                challenger_id,
                "rematch_challenge_accepted",
                "Rematch Accepted",
                f"{opponent_name} accepted your rematch challenge!",
                actor_id=opponent_id,
                target_type="rematch_challenge",
                target_id=challenge_id,
                metadata={"room_code": new_room_code, "mode": mode},
                deduplication_key=f"rematch_acc:{challenge_id}",
                source_event_id=challenge_id,
            )

        return {
            "challenge_id": challenge_id,
            "status": "accepted",
            "room_code": new_room_code,
            "token": joined["token"],
            "player_id": joined["player_id"],
            "state": joined["state"],
            "joined": joined,
        }

    def decline_challenge(self, challenge_id: str, opponent_id: str) -> dict[str, Any]:
        """Decline a pending rematch challenge."""
        now = utc_now()
        with self._database.sessions.begin() as session:
            chal = session.get(RematchChallenge, challenge_id)
            if chal is None or chal.opponent_id != opponent_id:
                raise InvitationError("challenge_not_found", "The rematch challenge was not found.", 404)

            if chal.status != "pending":
                raise InvitationError("invalid_challenge_status", f"Cannot decline challenge with status '{chal.status}'.", 400)

            chal.status = "declined"
            chal.responded_at = now
            challenger_id = chal.challenger_id

        if self.notification_service is not None:
            self.notification_service.create_notification(
                challenger_id,
                "rematch_challenge_declined",
                "Rematch Declined",
                "Your rematch challenge was declined.",
                actor_id=opponent_id,
                target_type="rematch_challenge",
                target_id=challenge_id,
                deduplication_key=f"rematch_dec:{challenge_id}",
                source_event_id=challenge_id,
            )

        return {"challenge_id": challenge_id, "status": "declined"}

    def cancel_challenge(self, challenge_id: str, challenger_id: str) -> dict[str, Any]:
        """Cancel a pending outgoing rematch challenge."""
        now = utc_now()
        with self._database.sessions.begin() as session:
            chal = session.get(RematchChallenge, challenge_id)
            if chal is None or chal.challenger_id != challenger_id:
                raise InvitationError("challenge_not_found", "The rematch challenge was not found.", 404)

            if chal.status != "pending":
                raise InvitationError("invalid_challenge_status", f"Cannot cancel challenge with status '{chal.status}'.", 400)

            chal.status = "cancelled"
            chal.responded_at = now

        return {"challenge_id": challenge_id, "status": "cancelled"}

"""Social multiplayer persistence services: player discovery, friendships, friend requests, and blocking."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import aliased

from quizverse.persistence.competition import AdvancedStatsService, HonorsService
from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    FriendRequest,
    Friendship,
    PlayerBlock,
    PlayerProfile,
    PlayerRating,
    utc_now,
)
from quizverse.persistence.ratings import RatingService


class SocialError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class BlockService:
    """Manages player blocking and mutual social interaction suppression."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def block_player(self, blocker_id: str, target_id: str) -> dict[str, Any]:
        if blocker_id == target_id:
            raise SocialError("cannot_block_self", "You cannot block yourself.", 400)

        with self._database.sessions.begin() as session:
            target = session.get(PlayerProfile, target_id)
            if target is None or target.status != "active":
                raise SocialError("player_not_found", "The specified player profile was not found or is inactive.", 404)

            # Idempotency check
            existing = session.scalar(
                select(PlayerBlock).where(
                    PlayerBlock.blocker_id == blocker_id,
                    PlayerBlock.blocked_id == target_id,
                )
            )
            now = utc_now()
            if existing is None:
                block_row = PlayerBlock(
                    id=secrets.token_hex(16),
                    blocker_id=blocker_id,
                    blocked_id=target_id,
                    created_at=now,
                )
                session.add(block_row)

            # 1. Sever any active friendship in either direction
            p1, p2 = (blocker_id, target_id) if blocker_id < target_id else (target_id, blocker_id)
            session.execute(
                delete(Friendship).where(
                    Friendship.player_one_id == p1,
                    Friendship.player_two_id == p2,
                )
            )

            # 2. Invalidate any pending friend requests in both directions
            session.execute(
                update(FriendRequest)
                .where(
                    or_(
                        (FriendRequest.sender_id == blocker_id) & (FriendRequest.recipient_id == target_id),
                        (FriendRequest.sender_id == target_id) & (FriendRequest.recipient_id == blocker_id),
                    ),
                    FriendRequest.status == "pending",
                )
                .values(status="cancelled", updated_at=now)
            )

            return {
                "blocked_id": target_id,
                "display_name": target.display_name,
                "created_at": now,
            }

    def unblock_player(self, blocker_id: str, target_id: str) -> bool:
        with self._database.sessions.begin() as session:
            result = session.execute(
                delete(PlayerBlock).where(
                    PlayerBlock.blocker_id == blocker_id,
                    PlayerBlock.blocked_id == target_id,
                )
            )
            return result.rowcount > 0

    def is_blocked(self, p1: str, p2: str, session=None) -> bool:
        """Returns True if either player has blocked the other."""
        query = select(PlayerBlock.id).where(
            or_(
                (PlayerBlock.blocker_id == p1) & (PlayerBlock.blocked_id == p2),
                (PlayerBlock.blocker_id == p2) & (PlayerBlock.blocked_id == p1),
            )
        ).limit(1)

        if session is not None:
            return session.scalar(query) is not None

        with self._database.session() as s:
            return s.scalar(query) is not None

    def get_blocking_and_blocked_ids(self, player_id: str, session=None) -> set[str]:
        """Returns set of all player IDs who blocked player_id OR were blocked by player_id."""
        query_blocked = select(PlayerBlock.blocked_id).where(PlayerBlock.blocker_id == player_id)
        query_blockers = select(PlayerBlock.blocker_id).where(PlayerBlock.blocked_id == player_id)

        if session is not None:
            s1 = set(session.scalars(query_blocked).all())
            s2 = set(session.scalars(query_blockers).all())
            return s1 | s2

        with self._database.session() as s:
            s1 = set(s.scalars(query_blocked).all())
            s2 = set(s.scalars(query_blockers).all())
            return s1 | s2

    def list_blocked_players(self, blocker_id: str) -> list[dict[str, Any]]:
        with self._database.session() as session:
            rows = session.execute(
                select(PlayerBlock, PlayerProfile)
                .join(PlayerProfile, PlayerProfile.id == PlayerBlock.blocked_id)
                .where(PlayerBlock.blocker_id == blocker_id)
                .order_by(PlayerBlock.created_at.desc())
            ).all()

            return [
                {
                    "player_id": profile.id,
                    "display_name": profile.display_name,
                    "blocked_at": block.created_at,
                }
                for block, profile in rows
            ]


class FriendService:
    """Manages the full friend request lifecycle and canonical friendships."""

    def __init__(
        self,
        database: Database,
        block_service: BlockService,
        rating_service: RatingService | None = None,
    ) -> None:
        self._database = database
        self._block_service = block_service
        self._rating_service = rating_service or RatingService(database)

    def are_friends(self, p1: str, p2: str, session=None) -> bool:
        if p1 == p2:
            return False
        pair1, pair2 = (p1, p2) if p1 < p2 else (p2, p1)
        query = select(Friendship.id).where(
            Friendship.player_one_id == pair1,
            Friendship.player_two_id == pair2,
        ).limit(1)

        if session is not None:
            return session.scalar(query) is not None

        with self._database.session() as s:
            return s.scalar(query) is not None

    def send_request(self, sender_id: str, recipient_id: str) -> dict[str, Any]:
        if sender_id == recipient_id:
            raise SocialError("cannot_friend_self", "You cannot send a friend request to yourself.", 400)

        with self._database.sessions.begin() as session:
            recipient = session.get(PlayerProfile, recipient_id)
            if recipient is None or recipient.status != "active":
                raise SocialError("player_not_found", "The specified player profile was not found or is inactive.", 404)

            # Block check
            if self._block_service.is_blocked(sender_id, recipient_id, session=session):
                raise SocialError("blocked_player", "Cannot send a friend request due to player privacy restrictions.", 403)

            # Already friends check
            if self.are_friends(sender_id, recipient_id, session=session):
                raise SocialError("already_friends", "You are already friends with this player.", 400)

            now = utc_now()

            # Check existing pending request from sender to recipient
            existing_outgoing = session.scalar(
                select(FriendRequest).where(
                    FriendRequest.sender_id == sender_id,
                    FriendRequest.recipient_id == recipient_id,
                    FriendRequest.status == "pending",
                )
            )
            if existing_outgoing is not None:
                raise SocialError("duplicate_request", "A friend request is already pending for this player.", 400)

            # Check opposite pending request (from recipient to sender): simultaneous request auto-accept!
            reverse_pending = session.scalar(
                select(FriendRequest).where(
                    FriendRequest.sender_id == recipient_id,
                    FriendRequest.recipient_id == sender_id,
                    FriendRequest.status == "pending",
                )
            )
            if reverse_pending is not None:
                reverse_pending.status = "accepted"
                reverse_pending.updated_at = now
                p1, p2 = (sender_id, recipient_id) if sender_id < recipient_id else (recipient_id, sender_id)
                session.add(
                    Friendship(
                        id=secrets.token_hex(16),
                        player_one_id=p1,
                        player_two_id=p2,
                        created_at=now,
                    )
                )
                return {
                    "request_id": reverse_pending.id,
                    "status": "accepted",
                    "friendship_created": True,
                    "recipient_id": recipient_id,
                    "recipient_name": recipient.display_name,
                    "created_at": now,
                }

            # Create new pending friend request
            request_row = FriendRequest(
                id=secrets.token_hex(16),
                sender_id=sender_id,
                recipient_id=recipient_id,
                status="pending",
                created_at=now,
                updated_at=now,
            )
            session.add(request_row)
            return {
                "request_id": request_row.id,
                "status": "pending",
                "friendship_created": False,
                "recipient_id": recipient_id,
                "recipient_name": recipient.display_name,
                "created_at": now,
            }

    def accept_request(self, request_id: str, recipient_id: str) -> dict[str, Any]:
        with self._database.sessions.begin() as session:
            req = session.get(FriendRequest, request_id)
            if req is None or req.recipient_id != recipient_id:
                raise SocialError("request_not_found", "The friend request was not found.", 404)

            if req.status != "pending":
                raise SocialError("invalid_request_status", f"Cannot accept friend request with status '{req.status}'.", 400)

            # Block check
            if self._block_service.is_blocked(req.sender_id, req.recipient_id, session=session):
                req.status = "cancelled"
                req.updated_at = utc_now()
                raise SocialError("blocked_player", "Cannot accept request from a blocked player.", 403)

            now = utc_now()
            req.status = "accepted"
            req.updated_at = now

            # Canonical pair creation
            p1, p2 = (req.sender_id, req.recipient_id) if req.sender_id < req.recipient_id else (req.recipient_id, req.sender_id)
            existing_f = session.scalar(
                select(Friendship).where(
                    Friendship.player_one_id == p1,
                    Friendship.player_two_id == p2,
                )
            )
            if existing_f is None:
                friendship = Friendship(
                    id=secrets.token_hex(16),
                    player_one_id=p1,
                    player_two_id=p2,
                    created_at=now,
                )
                session.add(friendship)

            # Invalidate any duplicate pending requests between the pair
            session.execute(
                update(FriendRequest)
                .where(
                    or_(
                        (FriendRequest.sender_id == p1) & (FriendRequest.recipient_id == p2),
                        (FriendRequest.sender_id == p2) & (FriendRequest.recipient_id == p1),
                    ),
                    FriendRequest.id != req.id,
                    FriendRequest.status == "pending",
                )
                .values(status="cancelled", updated_at=now)
            )

            sender = session.get(PlayerProfile, req.sender_id)
            return {
                "request_id": req.id,
                "status": "accepted",
                "friend_id": req.sender_id,
                "friend_name": sender.display_name if sender else "Player",
                "accepted_at": now,
            }

    def reject_request(self, request_id: str, recipient_id: str) -> dict[str, Any]:
        with self._database.sessions.begin() as session:
            req = session.get(FriendRequest, request_id)
            if req is None or req.recipient_id != recipient_id:
                raise SocialError("request_not_found", "The friend request was not found.", 404)

            if req.status != "pending":
                raise SocialError("invalid_request_status", f"Cannot reject friend request with status '{req.status}'.", 400)

            now = utc_now()
            req.status = "rejected"
            req.updated_at = now
            return {
                "request_id": req.id,
                "status": "rejected",
                "updated_at": now,
            }

    def cancel_request(self, request_id: str, sender_id: str) -> dict[str, Any]:
        with self._database.sessions.begin() as session:
            req = session.get(FriendRequest, request_id)
            if req is None or req.sender_id != sender_id:
                raise SocialError("request_not_found", "The friend request was not found.", 404)

            if req.status != "pending":
                raise SocialError("invalid_request_status", f"Cannot cancel friend request with status '{req.status}'.", 400)

            now = utc_now()
            req.status = "cancelled"
            req.updated_at = now
            return {
                "request_id": req.id,
                "status": "cancelled",
                "updated_at": now,
            }

    def remove_friend(self, player_id: str, friend_id: str) -> bool:
        if player_id == friend_id:
            return False

        p1, p2 = (player_id, friend_id) if player_id < friend_id else (friend_id, player_id)
        with self._database.sessions.begin() as session:
            result = session.execute(
                delete(Friendship).where(
                    Friendship.player_one_id == p1,
                    Friendship.player_two_id == p2,
                )
            )
            return result.rowcount > 0

    def list_friends(self, player_id: str) -> list[dict[str, Any]]:
        with self._database.session() as session:
            blocked_ids = self._block_service.get_blocking_and_blocked_ids(player_id, session=session)

            # Query all friendships where player_id is player_one or player_two
            f_rows = session.execute(
                select(Friendship).where(
                    or_(
                        Friendship.player_one_id == player_id,
                        Friendship.player_two_id == player_id,
                    )
                ).order_by(Friendship.created_at.desc())
            ).scalars().all()

            friend_pairs = [
                (f.player_two_id if f.player_one_id == player_id else f.player_one_id, f.created_at)
                for f in f_rows
            ]
            friend_pairs = [(fid, cat) for fid, cat in friend_pairs if fid not in blocked_ids]

            if not friend_pairs:
                return []

            f_ids = [fid for fid, _ in friend_pairs]
            cat_map = dict(friend_pairs)

            profiles = session.scalars(
                select(PlayerProfile).where(
                    PlayerProfile.id.in_(f_ids),
                    PlayerProfile.status == "active",
                )
            ).all()
            profile_map = {p.id: p for p in profiles}

            ratings = session.scalars(
                select(PlayerRating).where(
                    PlayerRating.player_id.in_(f_ids),
                    PlayerRating.rating_scope == "global",
                )
            ).all()
            rating_map = {r.player_id: r for r in ratings}

            results: list[dict[str, Any]] = []
            for fid in f_ids:
                prof = profile_map.get(fid)
                if not prof:
                    continue
                rat = rating_map.get(fid)
                rating_val = rat.rating if rat else 1000
                rated_count = rat.rated_match_count if rat else 0
                is_prov = rated_count < 10
                wins = rat.wins if rat else 0
                losses = rat.losses if rat else 0
                draws = rat.draws if rat else 0
                tot = wins + losses + draws
                win_pct = round((wins / tot) * 100.0, 1) if tot > 0 else 0.0

                results.append({
                    "player_id": prof.id,
                    "display_name": prof.display_name,
                    "rating": rating_val,
                    "is_provisional": is_prov,
                    "rated_matches": rated_count,
                    "wins": wins,
                    "losses": losses,
                    "draws": draws,
                    "win_percentage": win_pct,
                    "friended_at": cat_map.get(fid),
                    "presence": {"status": "unavailable"},
                })

            return results

    def list_incoming_requests(self, player_id: str) -> list[dict[str, Any]]:
        with self._database.session() as session:
            blocked_ids = self._block_service.get_blocking_and_blocked_ids(player_id, session=session)

            rows = session.execute(
                select(FriendRequest, PlayerProfile)
                .join(PlayerProfile, PlayerProfile.id == FriendRequest.sender_id)
                .where(
                    FriendRequest.recipient_id == player_id,
                    FriendRequest.status == "pending",
                    PlayerProfile.status == "active",
                )
                .order_by(FriendRequest.created_at.desc())
            ).all()

            results: list[dict[str, Any]] = []
            for req, sender in rows:
                if sender.id in blocked_ids:
                    continue
                rating = session.scalar(
                    select(PlayerRating.rating).where(
                        PlayerRating.player_id == sender.id,
                        PlayerRating.rating_scope == "global",
                    )
                ) or 1000
                results.append({
                    "request_id": req.id,
                    "sender_id": sender.id,
                    "sender_name": sender.display_name,
                    "sender_display_name": sender.display_name,
                    "sender_rating": rating,
                    "created_at": req.created_at,
                })
            return results

    def list_outgoing_requests(self, player_id: str) -> list[dict[str, Any]]:
        with self._database.session() as session:
            blocked_ids = self._block_service.get_blocking_and_blocked_ids(player_id, session=session)

            rows = session.execute(
                select(FriendRequest, PlayerProfile)
                .join(PlayerProfile, PlayerProfile.id == FriendRequest.recipient_id)
                .where(
                    FriendRequest.sender_id == player_id,
                    FriendRequest.status == "pending",
                    PlayerProfile.status == "active",
                )
                .order_by(FriendRequest.created_at.desc())
            ).all()

            results: list[dict[str, Any]] = []
            for req, recipient in rows:
                if recipient.id in blocked_ids:
                    continue
                rating = session.scalar(
                    select(PlayerRating.rating).where(
                        PlayerRating.player_id == recipient.id,
                        PlayerRating.rating_scope == "global",
                    )
                ) or 1000
                results.append({
                    "request_id": req.id,
                    "recipient_id": recipient.id,
                    "recipient_name": recipient.display_name,
                    "recipient_display_name": recipient.display_name,
                    "recipient_rating": rating,
                    "created_at": req.created_at,
                })
            return results

    def get_relationship_state(self, current_player_id: str, other_player_id: str, session=None) -> str:
        if current_player_id == other_player_id:
            return "self"

        def _check(s):
            # 1. Block check
            b = s.scalar(
                select(PlayerBlock.blocker_id).where(
                    or_(
                        (PlayerBlock.blocker_id == current_player_id) & (PlayerBlock.blocked_id == other_player_id),
                        (PlayerBlock.blocker_id == other_player_id) & (PlayerBlock.blocked_id == current_player_id),
                    )
                ).limit(1)
            )
            if b is not None:
                return "blocked" if b == current_player_id else "blocked_by"

            # 2. Friend check
            p1, p2 = (current_player_id, other_player_id) if current_player_id < other_player_id else (other_player_id, current_player_id)
            f = s.scalar(
                select(Friendship.id).where(
                    Friendship.player_one_id == p1,
                    Friendship.player_two_id == p2,
                ).limit(1)
            )
            if f is not None:
                return "friends"

            # 3. Pending requests
            req = s.execute(
                select(FriendRequest.sender_id).where(
                    or_(
                        (FriendRequest.sender_id == current_player_id) & (FriendRequest.recipient_id == other_player_id),
                        (FriendRequest.sender_id == other_player_id) & (FriendRequest.recipient_id == current_player_id),
                    ),
                    FriendRequest.status == "pending",
                ).limit(1)
            ).scalar()

            if req is not None:
                return "outgoing_request" if req == current_player_id else "incoming_request"

            return "none"

        if session is not None:
            return _check(session)
        with self._database.session() as s:
            return _check(s)


class PlayerDiscoveryService:
    """Provides player search, public discovery cards, and public profiles."""

    def __init__(
        self,
        database: Database,
        friend_service: FriendService,
        block_service: BlockService,
        stats_service: AdvancedStatsService,
        honors_service: HonorsService,
    ) -> None:
        self._database = database
        self._friend_service = friend_service
        self._block_service = block_service
        self._stats_service = stats_service
        self._honors_service = honors_service

    def search_players(self, searcher_id: str, raw_query: str, limit: int = 20) -> list[dict[str, Any]]:
        query = (raw_query or "").strip()
        if len(query) < 2:
            raise SocialError("query_too_short", "Search query must contain at least 2 characters.", 422)

        bounded_limit = max(1, min(limit, 20))

        with self._database.session() as session:
            blocked_ids = self._block_service.get_blocking_and_blocked_ids(searcher_id, session=session)

            # Check if query is 32-hex ID match
            is_hex_id = bool(re.fullmatch(r"^[0-9a-fA-F]{32}$", query))

            base = (
                select(PlayerProfile)
                .where(
                    PlayerProfile.id != searcher_id,
                    PlayerProfile.status == "active",
                )
            )
            if blocked_ids:
                base = base.where(PlayerProfile.id.notin_(blocked_ids))

            if is_hex_id:
                stmt = base.where(
                    or_(
                        PlayerProfile.id == query.lower(),
                        PlayerProfile.display_name.ilike(f"%{query}%"),
                    )
                )
            else:
                stmt = base.where(PlayerProfile.display_name.ilike(f"%{query}%"))

            stmt = stmt.order_by(PlayerProfile.display_name.asc(), PlayerProfile.id.asc()).limit(bounded_limit)
            matched_profiles = session.scalars(stmt).all()

            if not matched_profiles:
                return []

            pids = [p.id for p in matched_profiles]

            # Ratings map
            ratings = session.scalars(
                select(PlayerRating).where(
                    PlayerRating.player_id.in_(pids),
                    PlayerRating.rating_scope == "global",
                )
            ).all()
            rating_map = {r.player_id: r for r in ratings}

            results: list[dict[str, Any]] = []
            for prof in matched_profiles:
                rat = rating_map.get(prof.id)
                rating_val = rat.rating if rat else 1000
                rated_count = rat.rated_match_count if rat else 0
                is_prov = rated_count < 10
                wins = rat.wins if rat else 0
                losses = rat.losses if rat else 0
                draws = rat.draws if rat else 0
                tot = wins + losses + draws
                win_pct = round((wins / tot) * 100.0, 1) if tot > 0 else 0.0

                rel_state = self._friend_service.get_relationship_state(searcher_id, prof.id, session=session)

                results.append({
                    "player_id": prof.id,
                    "display_name": prof.display_name,
                    "rating": rating_val,
                    "is_provisional": is_prov,
                    "total_matches": tot,
                    "wins": wins,
                    "losses": losses,
                    "draws": draws,
                    "win_percentage": win_pct,
                    "relationship_state": rel_state,
                })

            return results

    def get_public_profile(self, searcher_id: str, target_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            target = session.get(PlayerProfile, target_id)
            if target is None or target.status != "active":
                raise SocialError("player_not_found", "The specified player profile was not found or is inactive.", 404)

            # Block check
            if self._block_service.is_blocked(searcher_id, target_id, session=session):
                raise SocialError("blocked_player", "This player profile is unavailable due to privacy restrictions.", 403)

            rel_state = self._friend_service.get_relationship_state(searcher_id, target_id, session=session)

        stats = self._stats_service.get_advanced_stats(target_id)
        honors = self._honors_service.get_player_honors(target_id)

        return {
            "player_id": target.id,
            "display_name": target.display_name,
            "created_at": target.created_at,
            "relationship_state": rel_state,
            "rating": stats["current_rating"],
            "peak_rating": stats["peak_rating"],
            "is_provisional": stats["is_provisional"],
            "rated_matches": stats["rated_match_count"],
            "total_matches": stats["total_matches"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "draws": stats["draws"],
            "win_percentage": stats["win_percentage"],
            "current_streak": stats["current_streak"],
            "longest_win_streak": stats["longest_win_streak"],
            "accuracy_pct": stats["accuracy_pct"],
            "recent_form": stats["recent_form"],
            "honors_summary": {
                "tournament_championships": honors["tournament_championships"],
                "tournament_runner_ups": honors["tournament_runner_ups"],
                "league_championships": honors["league_championships"],
                "league_podiums": honors["league_podiums"],
                "total_honors": honors["total_honors"],
            },
        }

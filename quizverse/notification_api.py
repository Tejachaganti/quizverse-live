"""FastAPI endpoints for Notification Center and Social Intelligence."""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Query, status
from pydantic import BaseModel

from quizverse.persistence.notifications import (
    NotificationError,
    NotificationService,
    SocialIntelligenceService,
)
from quizverse.persistence.player_identity import PlayerIdentityError, PlayerIdentityService
from quizverse.presence import PresenceService
from quizverse.quiz_api import ApiProblem


class RateLimiter:
    """Sliding-window in-memory action rate limiter per player."""

    def __init__(self, limit: int = 60, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self.history: dict[str, list[float]] = {}

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.time()
        bucket = [t for t in self.history.get(key, []) if now - t < self.window]
        if len(bucket) >= self.limit:
            oldest = bucket[0]
            retry_after = max(1, int(self.window - (now - oldest)))
            return False, retry_after
        bucket.append(now)
        self.history[key] = bucket
        return True, 0


class UpdatePreferencesRequest(BaseModel):
    friend_requests: bool | None = None
    friendship_updates: bool | None = None
    friend_online: bool | None = None
    room_invitations: bool | None = None
    rematch_challenges: bool | None = None
    direct_message_notifications: bool | None = None
    tournament_updates: bool | None = None
    league_updates: bool | None = None
    achievement_notifications: bool | None = None
    rating_milestones: bool | None = None
    personal_best_notifications: bool | None = None
    browser_sound: bool | None = None
    visual_notifications: bool | None = None


def _authenticated_header(authorization: str | None) -> str:
    if authorization is None:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not 40 <= len(token.strip()) <= 128:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    return token.strip()


def install_notification_api(
    app: FastAPI,
    notification_service: NotificationService,
    social_intelligence_service: SocialIntelligenceService,
    identities: PlayerIdentityService,
    presence_service: PresenceService,
) -> None:
    router = APIRouter(prefix="/api/notifications", tags=["notifications"])
    action_limiter = RateLimiter(limit=100, window_seconds=60.0)

    def _require_player(authorization: str | None) -> str:
        token = _authenticated_header(authorization)
        try:
            player = identities.authenticate(token)
            return str(player["id"])
        except PlayerIdentityError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.get("", status_code=status.HTTP_200_OK)
    def list_notifications(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        unread_only: bool = False,
        category: str | None = None,
        filter: str | None = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Fetch paginated notifications for the authenticated player."""
        player_id = _require_player(authorization)
        cat = category or filter
        try:
            return notification_service.list_notifications(
                player_id,
                limit=limit,
                offset=offset,
                unread_only=unread_only,
                category=cat,
            )
        except NotificationError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.get("/unread-count", status_code=status.HTTP_200_OK)
    def get_unread_count(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Fetch current unread notification count for player."""
        player_id = _require_player(authorization)
        count = notification_service.get_unread_count(player_id)
        return {"unread_count": count}

    @router.get("/summary", status_code=status.HTTP_200_OK)
    def get_notification_summary(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Fetch summary of notifications including unread counts by category."""
        player_id = _require_player(authorization)
        return notification_service.get_summary(player_id)

    @router.post("/{notification_id}/read", status_code=status.HTTP_200_OK)
    def mark_notification_read(
        notification_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Mark a single notification as read."""
        player_id = _require_player(authorization)
        allowed, retry_after = action_limiter.allow(player_id)
        if not allowed:
            raise ApiProblem(429, "rate_limited", f"Please wait {retry_after}s before performing more notification actions.")
        try:
            notif = notification_service.mark_read(player_id, notification_id)
            return {"ok": True, "notification": notif, "is_read": True}
        except NotificationError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.post("/{notification_id}/unread", status_code=status.HTTP_200_OK)
    def mark_notification_unread(
        notification_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Mark a single notification as unread."""
        player_id = _require_player(authorization)
        allowed, retry_after = action_limiter.allow(player_id)
        if not allowed:
            raise ApiProblem(429, "rate_limited", f"Please wait {retry_after}s before performing more notification actions.")
        try:
            notif = notification_service.mark_unread(player_id, notification_id)
            return {"ok": True, "notification": notif, "is_read": False}
        except NotificationError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.post("/read-all", status_code=status.HTTP_200_OK)
    @router.post("/mark-all-read", status_code=status.HTTP_200_OK)
    def mark_all_notifications_read(
        category: str | None = None,
        filter: str | None = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Mark all unread notifications (or in a specific category) as read."""
        player_id = _require_player(authorization)
        allowed, retry_after = action_limiter.allow(player_id)
        if not allowed:
            raise ApiProblem(429, "rate_limited", f"Please wait {retry_after}s before performing more notification actions.")
        cat = category or filter
        updated_count = notification_service.mark_all_read(player_id, category=cat)
        return {"ok": True, "updated_count": updated_count, "marked_read_count": updated_count}

    @router.delete("/clear-read", status_code=status.HTTP_200_OK)
    @router.delete("/read", status_code=status.HTTP_200_OK)
    def delete_read_notifications(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Bulk clear all read notifications for player."""
        player_id = _require_player(authorization)
        allowed, retry_after = action_limiter.allow(player_id)
        if not allowed:
            raise ApiProblem(429, "rate_limited", f"Please wait {retry_after}s before performing more notification actions.")
        deleted_count = notification_service.delete_read_notifications(player_id)
        return {"ok": True, "deleted_count": deleted_count, "cleared_count": deleted_count}

    @router.delete("/{notification_id}", status_code=status.HTTP_200_OK)
    def delete_notification(
        notification_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Dismiss or delete a single notification."""
        player_id = _require_player(authorization)
        allowed, retry_after = action_limiter.allow(player_id)
        if not allowed:
            raise ApiProblem(429, "rate_limited", f"Please wait {retry_after}s before performing more notification actions.")
        found = notification_service.delete_notification(player_id, notification_id)
        if not found:
            raise ApiProblem(404, "not_found", "Notification not found.")
        return {"ok": True, "deleted": True}

    @router.get("/preferences", status_code=status.HTTP_200_OK)
    def get_notification_preferences(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Retrieve current notification category and channel preferences."""
        player_id = _require_player(authorization)
        prefs = notification_service.get_preferences(player_id)
        return {**prefs, "preferences": prefs}

    @router.patch("/preferences", status_code=status.HTTP_200_OK)
    def update_notification_preferences(
        payload: UpdatePreferencesRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Update notification preferences for the authenticated player."""
        player_id = _require_player(authorization)
        updates = {k: v for k, v in payload.model_dump().items() if v is not None}
        try:
            prefs = notification_service.update_preferences(player_id, updates)
            return {**prefs, "preferences": prefs}
        except NotificationError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.get("/social-overview", status_code=status.HTTP_200_OK)
    def get_social_overview(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Fetch Social Intelligence Overview (friends count, online friends, pending actions, recent activity)."""
        player_id = _require_player(authorization)
        return social_intelligence_service.get_social_overview(player_id)

    app.include_router(router)

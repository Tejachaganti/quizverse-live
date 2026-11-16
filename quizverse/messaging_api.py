"""FastAPI HTTP API router for 1-on-1 private messaging, reactions, typing, and safety reporting."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Query, status
from pydantic import BaseModel, Field

from quizverse.persistence.messaging import (
    ALLOWED_REACTIONS,
    ALLOWED_REPORT_REASONS,
    MessagingError,
    MessagingService,
)
from quizverse.persistence.player_identity import (
    PlayerIdentityError,
    PlayerIdentityService,
    ProcessRateLimiter,
)
from quizverse.presence import PresenceService
from quizverse.quiz_api import ApiProblem

logger = logging.getLogger(__name__)


class CreateConversationRequest(BaseModel):
    friend_id: str = Field(min_length=32, max_length=32)


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class EditMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class AddReactionRequest(BaseModel):
    reaction: str = Field(min_length=1, max_length=16)


class ReportMessageRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=32)
    details: str | None = Field(default=None, max_length=1000)


class TypingRequest(BaseModel):
    conversation_id: str = Field(min_length=16, max_length=32)
    is_typing: bool = True


def _authenticated_header(authorization: str | None) -> str:
    if authorization is None:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not 40 <= len(token.strip()) <= 128:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    return token.strip()


def install_messaging_api(
    app: FastAPI,
    messaging_service: MessagingService,
    identities: PlayerIdentityService,
    presence_service: PresenceService,
) -> None:
    router = APIRouter(prefix="/api/messages", tags=["messaging"])

    # Rate limiters
    send_message_limiter = ProcessRateLimiter(limit=20, window_seconds=60)
    reactions_limiter = ProcessRateLimiter(limit=20, window_seconds=60)
    typing_limiter = ProcessRateLimiter(limit=10, window_seconds=5)

    def _require_player(authorization: str | None) -> str:
        token = _authenticated_header(authorization)
        try:
            player = identities.authenticate(token)
            return str(player["id"])
        except PlayerIdentityError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.get("/conversations", status_code=status.HTTP_200_OK)
    def list_conversations(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """List active conversations for the authenticated player."""
        player_id = _require_player(authorization)
        try:
            conversations = messaging_service.list_conversations(player_id)
            return {"conversations": conversations}
        except MessagingError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.post("/conversations", status_code=status.HTTP_200_OK)
    def get_or_create_conversation(
        payload: CreateConversationRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Start or retrieve a 1-on-1 direct conversation with an accepted friend."""
        player_id = _require_player(authorization)
        try:
            return messaging_service.get_or_create_conversation(player_id, payload.friend_id)
        except MessagingError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.get("/conversations/{conversation_id}", status_code=status.HTTP_200_OK)
    def get_conversation(
        conversation_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Retrieve conversation details and friend status."""
        player_id = _require_player(authorization)
        try:
            return messaging_service.get_conversation(conversation_id, player_id)
        except MessagingError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.get("/conversations/{conversation_id}/messages", status_code=status.HTTP_200_OK)
    def list_messages(
        conversation_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        before_id: str | None = Query(default=None),
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Fetch paginated message history for an authorized conversation."""
        player_id = _require_player(authorization)
        try:
            return messaging_service.list_messages(conversation_id, player_id, limit=limit, before_id=before_id)
        except MessagingError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.post("/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
    async def send_message(
        conversation_id: str,
        payload: SendMessageRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Send a new direct message to the other participant."""
        player_id = _require_player(authorization)

        allowed, retry_after = send_message_limiter.allow(player_id)
        if not allowed:
            raise ApiProblem(429, "rate_limited", f"Message rate limit exceeded. Try again in {retry_after}s.")

        try:
            result = messaging_service.send_message(conversation_id, player_id, payload.content)
            recipient_id = result.get("recipient_id")

            # Real-time delivery attempt via PresenceService
            delivered = False
            if recipient_id:
                delivered = await presence_service.send_direct_message_notification(recipient_id, {
                    "id": result["id"],
                    "conversation_id": conversation_id,
                    "sender_id": player_id,
                    "sender_name": result["sender_name"],
                    "content": result["content"],
                    "created_at": result["created_at"],
                    "status": "delivered",
                })

            if delivered and recipient_id:
                # Mark delivered in DB and send delivery receipt back to sender
                messaging_service.mark_delivered(result["id"], recipient_id)
                result["status"] = "delivered"
                await presence_service.send_direct_message_delivery_receipt(player_id, {
                    "id": result["id"],
                    "conversation_id": conversation_id,
                    "status": "delivered",
                })

            return result
        except MessagingError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.patch("/{message_id}", status_code=status.HTTP_200_OK)
    async def edit_message(
        message_id: str,
        payload: EditMessageRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Edit a message authored by the player within 5 minutes."""
        player_id = _require_player(authorization)
        try:
            result = messaging_service.edit_message(message_id, player_id, payload.content)
            recipient_id = result.get("recipient_id")
            if recipient_id:
                await presence_service.send_direct_message_edited(recipient_id, {
                    "id": message_id,
                    "conversation_id": result["conversation_id"],
                    "content": result["content"],
                    "edited_at": result["edited_at"],
                })
            return result
        except MessagingError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.delete("/{message_id}", status_code=status.HTTP_200_OK)
    async def delete_message(
        message_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Soft-delete a message authored by the player."""
        player_id = _require_player(authorization)
        try:
            result = messaging_service.delete_message(message_id, player_id)
            recipient_id = result.get("recipient_id")
            if recipient_id:
                await presence_service.send_direct_message_deleted(recipient_id, {
                    "id": message_id,
                    "conversation_id": result["conversation_id"],
                    "deleted": True,
                    "deleted_at": result["deleted_at"],
                })
            return result
        except MessagingError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.post("/{message_id}/read", status_code=status.HTTP_200_OK)
    @router.post("/conversations/{message_id}/read", status_code=status.HTTP_200_OK)
    async def mark_read(
        message_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Mark messages in a conversation as read."""
        player_id = _require_player(authorization)
        # Note: parameter message_id may be conversation_id when called via /conversations/{id}/read
        try:
            # First attempt conversation read directly
            try:
                res = messaging_service.mark_as_read(message_id, player_id)
            except MessagingError as e:
                if e.code == "conversation_not_found":
                    # message_id might be a message id, find conversation for this message
                    conv_details = messaging_service.get_conversation(message_id, player_id)
                    res = messaging_service.mark_as_read(conv_details["id"], player_id)
                else:
                    raise

            # Notify the sender that messages were read
            other_id = res.get("other_player_id")
            if other_id and res.get("marked_message_ids"):
                await presence_service.send_direct_message_read_receipt(other_id, {
                    "conversation_id": res["conversation_id"],
                    "reader_id": player_id,
                    "marked_message_ids": res["marked_message_ids"],
                    "read_at": res["read_at"],
                })
            return res
        except MessagingError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.post("/{message_id}/reactions", status_code=status.HTTP_200_OK)
    async def add_reaction(
        message_id: str,
        payload: AddReactionRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Add an emoji reaction to a direct message."""
        player_id = _require_player(authorization)
        allowed, retry_after = reactions_limiter.allow(player_id)
        if not allowed:
            raise ApiProblem(429, "rate_limited", f"Reaction rate limit exceeded. Try again in {retry_after}s.")

        try:
            res = messaging_service.add_reaction(message_id, player_id, payload.reaction)
            # Broadcast reaction update to both participants
            await presence_service.send_direct_message_reaction(
                [player_id, res["other_player_id"]],
                {
                    "message_id": message_id,
                    "conversation_id": res["conversation_id"],
                    "player_id": player_id,
                    "reaction": payload.reaction,
                    "action": "added",
                    "reactions": res["reactions"],
                },
            )
            return res
        except MessagingError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.delete("/{message_id}/reactions/{reaction}", status_code=status.HTTP_200_OK)
    async def remove_reaction(
        message_id: str,
        reaction: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Remove an emoji reaction previously added by the player."""
        player_id = _require_player(authorization)
        try:
            res = messaging_service.remove_reaction(message_id, player_id, reaction)
            # Broadcast reaction update to both participants
            await presence_service.send_direct_message_reaction(
                [player_id, res["other_player_id"]],
                {
                    "message_id": message_id,
                    "conversation_id": res["conversation_id"],
                    "player_id": player_id,
                    "reaction": reaction,
                    "action": "removed",
                    "reactions": res["reactions"],
                },
            )
            return res
        except MessagingError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.post("/{message_id}/report", status_code=status.HTTP_201_CREATED)
    def report_message(
        message_id: str,
        payload: ReportMessageRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Submit a safety report against an inappropriate direct message."""
        player_id = _require_player(authorization)
        try:
            return messaging_service.report_message(message_id, player_id, payload.reason, payload.details)
        except MessagingError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    @router.post("/typing", status_code=status.HTTP_200_OK)
    async def emit_typing(
        payload: TypingRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Emit an ephemeral typing indicator to the conversation partner."""
        player_id = _require_player(authorization)
        rate_key = f"{player_id}:{payload.conversation_id}"
        allowed, retry_after = typing_limiter.allow(rate_key)
        if not allowed:
            raise ApiProblem(429, "rate_limited", f"Typing indicator rate limit exceeded. Try again in {retry_after}s.")

        try:
            conv = messaging_service.get_conversation(payload.conversation_id, player_id)
            friend_id = conv["friend"]["player_id"]
            event_type = "typing_started" if payload.is_typing else "typing_stopped"
            await presence_service.send_typing_indicator(friend_id, {
                "event": event_type,
                "conversation_id": payload.conversation_id,
                "player_id": player_id,
            })
            return {"ok": True, "event": event_type}
        except MessagingError as exc:
            raise ApiProblem(exc.status_code, exc.code, exc.message) from exc

    app.include_router(router)

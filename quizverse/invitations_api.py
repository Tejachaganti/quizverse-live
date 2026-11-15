"""FastAPI HTTP API router for room invitations, secure admission, and casual direct rematches."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Self

from fastapi import APIRouter, FastAPI, Header, Request, status
from pydantic import BaseModel, Field, model_validator

from quizverse.persistence.invitations import InvitationError, InvitationService, RematchService
from quizverse.persistence.player_identity import (
    PlayerIdentityError,
    PlayerIdentityService,
    ProcessRateLimiter,
)
from quizverse.quiz_api import ApiProblem

logger = logging.getLogger(__name__)


class CreateInvitationRequest(BaseModel):
    recipient_id: str | None = Field(default=None, min_length=32, max_length=32)
    invitee_id: str | None = Field(default=None, min_length=32, max_length=32)
    room_code: str = Field(min_length=6, max_length=16)

    @model_validator(mode="after")
    def populate_recipient(self) -> Self:
        target = self.recipient_id or self.invitee_id
        if not target:
            raise ValueError("recipient_id or invitee_id is required.")
        self.recipient_id = target
        return self


class AdmitInvitationRequest(BaseModel):
    admission_ticket: str = Field(min_length=20, max_length=128)
    player_name: str | None = Field(default=None, min_length=1, max_length=24)


class CreateRematchRequest(BaseModel):
    opponent_id: str = Field(min_length=32, max_length=32)
    original_match_id: str = Field(min_length=1, max_length=64)


def _authenticated_header(authorization: str | None) -> str:
    if authorization is None:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not 40 <= len(token.strip()) <= 128:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    return token.strip()


def install_invitations_api(
    app: FastAPI,
    invitation_service: InvitationService,
    rematch_service: RematchService,
    identities: PlayerIdentityService,
    engine: Any,
) -> None:
    router = APIRouter(prefix="/api/social", tags=["invitations_and_rematches"])
    invitation_limiter = ProcessRateLimiter(limit=5, window_seconds=60)
    rematch_limiter = ProcessRateLimiter(limit=5, window_seconds=60)

    async def get_current_player(authorization: str | None) -> dict[str, Any]:
        token = _authenticated_header(authorization)
        try:
            return await asyncio.to_thread(identities.authenticate, token)
        except PlayerIdentityError as error:
            raise ApiProblem(401, "invalid_player_credential", "The player credential is invalid or revoked.") from error

    # -------------------------------------------------------------------------
    # Room Invitations Endpoints
    # -------------------------------------------------------------------------

    @router.post("/invitations", status_code=status.HTTP_201_CREATED)
    async def create_room_invitation(
        body: CreateInvitationRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Send a friend-to-friend invitation to an existing waiting arena."""
        player = await get_current_player(authorization)
        player_id = str(player["id"])

        allowed, retry_after = invitation_limiter.allow(f"inv_create:{player_id}")
        if not allowed:
            raise ApiProblem(
                429,
                "rate_limited",
                "Too many invitations sent. Please slow down.",
                headers={"Retry-After": str(retry_after)},
            )

        try:
            return await invitation_service.create_invitation(
                inviter_id=player_id,
                recipient_id=body.recipient_id,
                room_code=body.room_code,
                engine=engine,
            )
        except InvitationError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.get("/invitations/incoming")
    async def list_incoming_invitations(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """View pending incoming invitations for the authenticated player."""
        player = await get_current_player(authorization)
        player_id = str(player["id"])
        invitations = await asyncio.to_thread(invitation_service.list_incoming_invitations, player_id)
        return {"incoming_invitations": invitations, "total": len(invitations)}

    @router.get("/invitations/outgoing")
    async def list_outgoing_invitations(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """View pending outgoing invitations sent by the authenticated player."""
        player = await get_current_player(authorization)
        player_id = str(player["id"])
        invitations = await asyncio.to_thread(invitation_service.list_outgoing_invitations, player_id)
        return {"outgoing_invitations": invitations, "total": len(invitations)}

    @router.post("/invitations/{invitation_id}/accept")
    async def accept_room_invitation(
        invitation_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Accept an incoming room invitation and obtain a single-use admission ticket."""
        player = await get_current_player(authorization)
        player_id = str(player["id"])

        try:
            return await invitation_service.accept_invitation(
                invitation_id=invitation_id,
                invitee_id=player_id,
                engine=engine,
            )
        except InvitationError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/invitations/{invitation_id}/join")
    async def join_room_with_invitation(
        invitation_id: str,
        body: AdmitInvitationRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Use an issued admission ticket to join the private arena authoritatively."""
        player = await get_current_player(authorization)
        player_id = str(player["id"])
        player_name = body.player_name or str(player.get("display_name", "Player"))

        try:
            return await invitation_service.admit_to_room(
                invitation_id=invitation_id,
                invitee_id=player_id,
                admission_ticket=body.admission_ticket,
                player_name=player_name,
                engine=engine,
            )
        except InvitationError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/invitations/{invitation_id}/decline")
    async def decline_room_invitation(
        invitation_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Decline a pending room invitation."""
        player = await get_current_player(authorization)
        player_id = str(player["id"])

        try:
            return await asyncio.to_thread(
                invitation_service.decline_invitation,
                invitation_id=invitation_id,
                invitee_id=player_id,
            )
        except InvitationError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.delete("/invitations/{invitation_id}")
    async def cancel_room_invitation(
        invitation_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Cancel an outgoing pending invitation."""
        player = await get_current_player(authorization)
        player_id = str(player["id"])

        try:
            return await asyncio.to_thread(
                invitation_service.cancel_invitation,
                invitation_id=invitation_id,
                inviter_id=player_id,
            )
        except InvitationError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    # -------------------------------------------------------------------------
    # Direct Casual Rematch Challenges Endpoints
    # -------------------------------------------------------------------------

    @router.post("/rematches", status_code=status.HTTP_201_CREATED)
    async def create_rematch_challenge(
        body: CreateRematchRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Challenge a previous casual opponent to a direct rematch."""
        player = await get_current_player(authorization)
        player_id = str(player["id"])

        allowed, retry_after = rematch_limiter.allow(f"rematch_create:{player_id}")
        if not allowed:
            raise ApiProblem(
                429,
                "rate_limited",
                "Too many rematch challenges sent. Please slow down.",
                headers={"Retry-After": str(retry_after)},
            )

        try:
            return await rematch_service.create_challenge(
                challenger_id=player_id,
                opponent_id=body.opponent_id,
                original_match_id=body.original_match_id,
            )
        except InvitationError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.get("/rematches/incoming")
    async def list_incoming_rematches(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """View pending incoming rematch challenges."""
        player = await get_current_player(authorization)
        player_id = str(player["id"])
        challenges = await asyncio.to_thread(rematch_service.list_incoming_challenges, player_id)
        return {"incoming_challenges": challenges, "total": len(challenges)}

    @router.get("/rematches/outgoing")
    async def list_outgoing_rematches(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """View pending outgoing rematch challenges."""
        player = await get_current_player(authorization)
        player_id = str(player["id"])
        challenges = await asyncio.to_thread(rematch_service.list_outgoing_challenges, player_id)
        return {"outgoing_challenges": challenges, "total": len(challenges)}

    @router.post("/rematches/{challenge_id}/accept")
    async def accept_rematch_challenge(
        challenge_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Accept a rematch challenge and create a fresh casual arena."""
        player = await get_current_player(authorization)
        player_id = str(player["id"])

        try:
            return await rematch_service.accept_challenge(
                challenge_id=challenge_id,
                opponent_id=player_id,
                engine=engine,
            )
        except InvitationError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/rematches/{challenge_id}/decline")
    async def decline_rematch_challenge(
        challenge_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Decline a pending rematch challenge."""
        player = await get_current_player(authorization)
        player_id = str(player["id"])

        try:
            return await asyncio.to_thread(
                rematch_service.decline_challenge,
                challenge_id=challenge_id,
                opponent_id=player_id,
            )
        except InvitationError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.delete("/rematches/{challenge_id}")
    async def cancel_rematch_challenge(
        challenge_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Cancel an outgoing pending rematch challenge."""
        player = await get_current_player(authorization)
        player_id = str(player["id"])

        try:
            return await asyncio.to_thread(
                rematch_service.cancel_challenge,
                challenge_id=challenge_id,
                challenger_id=player_id,
            )
        except InvitationError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    app.include_router(router)

"""FastAPI REST routes for player discovery, friend requests, friendships, and blocking."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from quizverse.persistence.player_identity import (
    PlayerIdentityError,
    PlayerIdentityService,
    ProcessRateLimiter,
)
from quizverse.persistence.social import (
    BlockService,
    FriendService,
    PlayerDiscoveryService,
    SocialError,
)
from quizverse.quiz_api import ApiProblem


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class SendFriendRequestBody(StrictRequest):
    recipient_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-fA-F]{32}$")


class BlockPlayerBody(StrictRequest):
    target_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-fA-F]{32}$")


def _authenticated_header(authorization: str | None) -> str:
    if authorization is None:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not 40 <= len(token.strip()) <= 128:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    return token.strip()


def install_social_api(
    app: FastAPI,
    discovery_service: PlayerDiscoveryService,
    friend_service: FriendService,
    block_service: BlockService,
    identities: PlayerIdentityService,
) -> None:
    router = APIRouter(prefix="/api")

    search_limiter = ProcessRateLimiter(limit=10, window_seconds=60)
    request_limiter = ProcessRateLimiter(limit=5, window_seconds=60)
    mutation_limiter = ProcessRateLimiter(limit=20, window_seconds=60)

    async def get_current_player_id(authorization: str | None) -> str:
        token = _authenticated_header(authorization)
        try:
            auth = await asyncio.to_thread(identities.authenticate, token)
            return str(auth["id"])
        except PlayerIdentityError as error:
            raise ApiProblem(401, "invalid_player_credential", "The player credential is invalid or revoked.") from error

    # 1. Player Discovery & Search
    @router.get("/players/search")
    async def search_players(
        request: Request,
        query: Annotated[str, Query(min_length=1, max_length=64)],
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        address = request.client.host if request.client else "unknown"
        allowed, retry_after = search_limiter.allow(f"search:{address}")
        if not allowed:
            raise ApiProblem(429, "rate_limited", "Too many search requests. Please slow down.", headers={"Retry-After": str(retry_after)})

        current_player_id = await get_current_player_id(authorization)
        try:
            results = await asyncio.to_thread(discovery_service.search_players, current_player_id, query, limit)
            return {"results": results, "total": len(results)}
        except SocialError as err:
            raise ApiProblem(err.status_code, err.code, err.message) from err

    @router.get("/players/{player_id}/public-profile")
    async def get_public_profile(
        player_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        if len(player_id) != 32:
            raise ApiProblem(422, "invalid_player_id", "Player profile ID must be a 32-character hexadecimal string.")
        current_player_id = await get_current_player_id(authorization)
        try:
            return await asyncio.to_thread(discovery_service.get_public_profile, current_player_id, player_id)
        except SocialError as err:
            raise ApiProblem(err.status_code, err.code, err.message) from err

    # 2. Friend Requests
    @router.post("/friends/requests")
    async def send_friend_request(
        body: SendFriendRequestBody,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        current_player_id = await get_current_player_id(authorization)
        allowed, retry_after = request_limiter.allow(f"freq:{current_player_id}")
        if not allowed:
            raise ApiProblem(429, "rate_limited", "Too many friend requests sent. Please slow down.", headers={"Retry-After": str(retry_after)})

        try:
            return await asyncio.to_thread(friend_service.send_request, current_player_id, body.recipient_id)
        except SocialError as err:
            raise ApiProblem(err.status_code, err.code, err.message) from err

    @router.get("/friends/requests/incoming")
    async def list_incoming_requests(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        current_player_id = await get_current_player_id(authorization)
        requests = await asyncio.to_thread(friend_service.list_incoming_requests, current_player_id)
        return {"incoming_requests": requests, "total": len(requests)}

    @router.get("/friends/requests/outgoing")
    async def list_outgoing_requests(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        current_player_id = await get_current_player_id(authorization)
        requests = await asyncio.to_thread(friend_service.list_outgoing_requests, current_player_id)
        return {"outgoing_requests": requests, "total": len(requests)}

    @router.post("/friends/requests/{request_id}/accept")
    async def accept_friend_request(
        request_id: str,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        current_player_id = await get_current_player_id(authorization)
        allowed, retry_after = mutation_limiter.allow(f"fmut:{current_player_id}")
        if not allowed:
            raise ApiProblem(429, "rate_limited", "Too many actions. Please slow down.", headers={"Retry-After": str(retry_after)})

        try:
            return await asyncio.to_thread(friend_service.accept_request, request_id, current_player_id)
        except SocialError as err:
            raise ApiProblem(err.status_code, err.code, err.message) from err

    @router.post("/friends/requests/{request_id}/reject")
    async def reject_friend_request(
        request_id: str,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        current_player_id = await get_current_player_id(authorization)
        allowed, retry_after = mutation_limiter.allow(f"fmut:{current_player_id}")
        if not allowed:
            raise ApiProblem(429, "rate_limited", "Too many actions. Please slow down.", headers={"Retry-After": str(retry_after)})

        try:
            return await asyncio.to_thread(friend_service.reject_request, request_id, current_player_id)
        except SocialError as err:
            raise ApiProblem(err.status_code, err.code, err.message) from err

    @router.delete("/friends/requests/{request_id}")
    async def cancel_friend_request(
        request_id: str,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        current_player_id = await get_current_player_id(authorization)
        allowed, retry_after = mutation_limiter.allow(f"fmut:{current_player_id}")
        if not allowed:
            raise ApiProblem(429, "rate_limited", "Too many actions. Please slow down.", headers={"Retry-After": str(retry_after)})

        try:
            return await asyncio.to_thread(friend_service.cancel_request, request_id, current_player_id)
        except SocialError as err:
            raise ApiProblem(err.status_code, err.code, err.message) from err

    # 3. Friendships
    @router.get("/friends")
    async def list_friends(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        current_player_id = await get_current_player_id(authorization)
        friends = await asyncio.to_thread(friend_service.list_friends, current_player_id)
        return {"friends": friends, "total": len(friends)}

    @router.delete("/friends/{friend_id}")
    async def remove_friend(
        friend_id: str,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        current_player_id = await get_current_player_id(authorization)
        allowed, retry_after = mutation_limiter.allow(f"fmut:{current_player_id}")
        if not allowed:
            raise ApiProblem(429, "rate_limited", "Too many actions. Please slow down.", headers={"Retry-After": str(retry_after)})

        removed = await asyncio.to_thread(friend_service.remove_friend, current_player_id, friend_id)
        if removed and hasattr(request.app.state, "presence_service"):
            await request.app.state.presence_service.notify_friendship_severed(current_player_id, friend_id)
        return {"removed": removed, "friend_id": friend_id}

    # 4. Player Blocking
    @router.get("/blocks")
    async def list_blocked_players(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        current_player_id = await get_current_player_id(authorization)
        blocked = await asyncio.to_thread(block_service.list_blocked_players, current_player_id)
        return {"blocked_players": blocked, "blocked": blocked, "total": len(blocked)}

    @router.post("/blocks")
    async def block_player(
        body: BlockPlayerBody,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        current_player_id = await get_current_player_id(authorization)
        allowed, retry_after = mutation_limiter.allow(f"fmut:{current_player_id}")
        if not allowed:
            raise ApiProblem(429, "rate_limited", "Too many actions. Please slow down.", headers={"Retry-After": str(retry_after)})

        try:
            res = await asyncio.to_thread(block_service.block_player, current_player_id, body.target_id)
            if hasattr(request.app.state, "presence_service"):
                await request.app.state.presence_service.notify_friendship_severed(current_player_id, body.target_id)
            return res
        except SocialError as err:
            raise ApiProblem(err.status_code, err.code, err.message) from err

    @router.delete("/blocks/{target_id}")
    async def unblock_player(
        target_id: str,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        current_player_id = await get_current_player_id(authorization)
        allowed, retry_after = mutation_limiter.allow(f"fmut:{current_player_id}")
        if not allowed:
            raise ApiProblem(429, "rate_limited", "Too many actions. Please slow down.", headers={"Retry-After": str(retry_after)})

        unblocked = await asyncio.to_thread(block_service.unblock_player, current_player_id, target_id)
        return {"unblocked": unblocked, "target_id": target_id}

    app.include_router(router)

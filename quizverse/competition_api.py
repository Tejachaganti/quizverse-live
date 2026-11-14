"""HTTP APIs for advanced player statistics, head-to-head comparison, and career honors."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from quizverse.persistence.competition import (
    AdvancedStatsService,
    CompetitionError,
    HeadToHeadService,
    HonorsService,
)
from quizverse.persistence.player_identity import (
    PlayerIdentityError,
    PlayerIdentityService,
    ProcessRateLimiter,
)
from quizverse.quiz_api import ApiProblem


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


def _authenticated_header(authorization: str | None) -> str:
    if authorization is None:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not 40 <= len(token.strip()) <= 128:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    return token.strip()


def install_competition_api(
    app: FastAPI,
    stats_service: AdvancedStatsService,
    h2h_service: HeadToHeadService,
    honors_service: HonorsService,
    identities: PlayerIdentityService,
) -> None:
    router = APIRouter(prefix="/api")
    limiter = ProcessRateLimiter(limit=20, window_seconds=60)

    def rate_limit(request: Request, action: str, identity: str = "") -> None:
        address = request.client.host if request.client else "unknown"
        allowed, retry_after = limiter.allow(f"{action}:{address}:{identity}")
        if not allowed:
            raise ApiProblem(429, "rate_limited", "Too many competition requests. Try again shortly.", headers={"Retry-After": str(retry_after)})

    async def resolve_player_id(player_id: str, authorization: str | None) -> str:
        if player_id.lower() == "me":
            token = _authenticated_header(authorization)
            try:
                auth = await asyncio.to_thread(identities.authenticate, token)
                return str(auth["id"])
            except PlayerIdentityError as error:
                raise ApiProblem(401, "invalid_player_credential", "The player credential is invalid or revoked.") from error
        if len(player_id) != 32:
            raise ApiProblem(422, "invalid_player_id", "Player profile ID must be a 32-character hexadecimal string.")
        return player_id

    @router.get("/players/{player_id}/statistics/advanced")
    async def get_advanced_statistics(
        player_id: str,
        request: Request,
        mode: str | None = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        rate_limit(request, "advanced_stats", player_id)
        resolved_id = await resolve_player_id(player_id, authorization)
        try:
            return await asyncio.to_thread(stats_service.get_advanced_stats, resolved_id, mode=mode)
        except CompetitionError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.get("/competition/head-to-head")
    async def get_head_to_head_comparison(
        request: Request,
        player_one_id: Annotated[str, Query(min_length=1, max_length=64)],
        player_two_id: Annotated[str, Query(min_length=1, max_length=64)],
        mode: str | None = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        rate_limit(request, "h2h", f"{player_one_id}:{player_two_id}")
        p1 = await resolve_player_id(player_one_id, authorization)
        p2 = await resolve_player_id(player_two_id, authorization)
        try:
            return await asyncio.to_thread(h2h_service.get_head_to_head, p1, p2, mode=mode)
        except CompetitionError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.get("/players/{player_id}/honors")
    async def get_player_honors(
        player_id: str,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        rate_limit(request, "honors", player_id)
        resolved_id = await resolve_player_id(player_id, authorization)
        try:
            return await asyncio.to_thread(honors_service.get_player_honors, resolved_id)
        except CompetitionError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    app.include_router(router)

"""HTTP APIs for opt-in player profiles and personal match history."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from quizverse.persistence.player_identity import (
    IssuedPlayerCredential,
    LiveIdentityTickets,
    MatchHistoryService,
    PlayerIdentityError,
    PlayerIdentityService,
    ProcessRateLimiter,
)
from quizverse.persistence.ratings import (
    ELIGIBLE_MODES,
    RatingService,
)
from quizverse.quiz_api import ApiProblem


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class PlayerProfileCreate(StrictRequest):
    display_name: str = Field(min_length=1, max_length=24)


class PlayerProfileUpdate(StrictRequest):
    display_name: str = Field(min_length=1, max_length=24)
    expected_version: int = Field(ge=1)


class PlayerProfileResponse(BaseModel):
    id: str
    display_name: str
    status: str
    version: int
    created_at: datetime | None
    updated_at: datetime | None


class CredentialIssueResponse(BaseModel):
    player: PlayerProfileResponse
    credential: str
    warning: str


_MODES = Literal[
    "classic", "speed_challenge", "team_battle", "elimination", "solo_practice",
    "quiz_royale", "power_play", "bluff_master", "escape_room",
]


class LeaderboardEntry(BaseModel):
    rank: int
    player_id: str
    display_name: str
    rating: int
    rated_match_count: int
    wins: int
    losses: int
    draws: int
    is_provisional: bool
    provisional: bool
    algorithm_version: str


class LeaderboardResponse(BaseModel):
    scope: str
    mode: str | None
    total: int
    established_total: int
    provisional_total: int
    limit: int
    offset: int
    cursor: str | None
    next_cursor: str | None
    has_more: bool
    as_of: datetime
    rankings: list[LeaderboardEntry]


class PlayerRatingDetail(BaseModel):
    rating: int
    rated_match_count: int
    wins: int
    losses: int
    draws: int
    is_provisional: bool
    provisional: bool
    algorithm_version: str
    updated_at: datetime


class RatingHistoryEntry(BaseModel):
    id: str
    scope: str
    match_id: str
    mode: str
    prior_rating: int
    rating_delta: int
    new_rating: int
    actual_score: float
    expected_score: float
    algorithm_version: str
    created_at: datetime


class PlayerRatingsSummaryResponse(BaseModel):
    player_id: str
    display_name: str
    global_rating: PlayerRatingDetail | None
    mode_ratings: dict[str, PlayerRatingDetail]
    recent_changes: list[RatingHistoryEntry]


class PlayerRatingHistoryResponse(BaseModel):
    total: int
    limit: int
    offset: int
    history: list[RatingHistoryEntry]


def _authenticated_header(authorization: str | None) -> str:
    if authorization is None:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not 40 <= len(token.strip()) <= 128:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    return token.strip()


def _credential_response(issued: IssuedPlayerCredential) -> CredentialIssueResponse:
    return CredentialIssueResponse(
        player=PlayerProfileResponse.model_validate({
            "id": issued.player_id, "display_name": issued.display_name, "status": "active",
            "version": issued.version,
            "created_at": issued.created_at,
            "updated_at": issued.updated_at,
        }),
        credential=issued.token,
        warning="This credential is shown once. Keep it private; if it is lost, the anonymous profile cannot be recovered.",
    )


def install_player_api(
    app: FastAPI,
    identities: PlayerIdentityService,
    history: MatchHistoryService,
    tickets: LiveIdentityTickets,
    ratings: RatingService | None = None,
) -> None:
    router = APIRouter(prefix="/api")
    rating_service = ratings or history.rating_service
    limiter = ProcessRateLimiter(limit=5, window_seconds=60)

    def rate_limit(request: Request, action: str, identity: str = "") -> None:
        address = request.client.host if request.client else "unknown"
        allowed, retry_after = limiter.allow(f"{action}:{address}:{identity}")
        if not allowed:
            raise ApiProblem(429, "player_rate_limited", "Too many profile requests. Try again shortly.", headers={"Retry-After": str(retry_after)})

    async def authenticated(authorization: str | None) -> dict[str, object]:
        token = _authenticated_header(authorization)
        try:
            return await asyncio.to_thread(identities.authenticate, token)
        except PlayerIdentityError as error:
            raise ApiProblem(401, "invalid_player_credential", "The player credential is invalid or revoked.") from error

    @router.post("/player-profiles", response_model=CredentialIssueResponse, status_code=201)
    async def create_profile(body: PlayerProfileCreate, request: Request, response: Response) -> CredentialIssueResponse:
        rate_limit(request, "create")
        try:
            issued = await asyncio.to_thread(identities.create, body.display_name)
        except PlayerIdentityError as error:
            raise ApiProblem(422, error.code, error.message) from error
        response.headers["Cache-Control"] = "no-store"
        return _credential_response(issued)

    @router.get("/player-profiles/me", response_model=PlayerProfileResponse)
    async def get_profile(authorization: Annotated[str | None, Header()] = None) -> dict[str, object]:
        return await authenticated(authorization)

    @router.patch("/player-profiles/me", response_model=PlayerProfileResponse)
    async def update_profile(body: PlayerProfileUpdate, authorization: Annotated[str | None, Header()] = None) -> dict[str, object]:
        player = await authenticated(authorization)
        try:
            return await asyncio.to_thread(identities.update_name, str(player["id"]), body.display_name, body.expected_version)
        except PlayerIdentityError as error:
            status = 409 if error.code == "version_conflict" else 404 if error.code == "player_not_found" else 422
            raise ApiProblem(status, error.code, error.message) from error

    @router.post("/player-profiles/me/credentials/rotate", response_model=CredentialIssueResponse)
    async def rotate_credential(
        request: Request,
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> CredentialIssueResponse:
        player = await authenticated(authorization)
        rate_limit(request, "rotate", str(player["id"]))
        try:
            issued = await asyncio.to_thread(identities.rotate, str(player["id"]))
        except PlayerIdentityError as error:
            raise ApiProblem(404, error.code, error.message) from error
        response.headers["Cache-Control"] = "no-store"
        return _credential_response(issued)

    @router.delete("/player-profiles/me", status_code=204)
    async def delete_profile(authorization: Annotated[str | None, Header()] = None) -> Response:
        player = await authenticated(authorization)
        await asyncio.to_thread(identities.anonymize, str(player["id"]))
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    @router.post("/player-profiles/me/live-ticket")
    async def issue_live_ticket(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        player = await authenticated(authorization)
        rate_limit(request, "ticket", str(player["id"]))
        return {"live_ticket": tickets.issue(str(player["id"])), "expires_in_seconds": 30}

    @router.get("/player-profiles/me/matches")
    async def list_my_matches(
        authorization: Annotated[str | None, Header()] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
        mode: _MODES | None = None,
    ) -> dict[str, object]:
        player = await authenticated(authorization)
        return await asyncio.to_thread(history.list_matches, str(player["id"]), limit=limit, offset=offset, mode=mode)

    @router.get("/player-profiles/me/competitive-stats")
    async def my_competitive_stats(authorization: Annotated[str | None, Header()] = None) -> dict[str, object]:
        player = await authenticated(authorization)
        return await asyncio.to_thread(history.stats, str(player["id"]))

    @router.get("/rankings", response_model=LeaderboardResponse)
    async def get_rankings(
        scope: Literal["global", "mode"] = "global",
        mode: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
        cursor: str | None = None,
    ) -> LeaderboardResponse:
        if scope == "mode":
            if not mode:
                raise ApiProblem(422, "invalid_ranking_scope", "A valid mode parameter is required when scope=mode.")
            if mode not in ELIGIBLE_MODES:
                raise ApiProblem(422, "invalid_ranking_mode", f"The mode '{mode}' is not eligible for competitive rankings.")
        else:
            mode = None

        try:
            data = await asyncio.to_thread(
                rating_service.leaderboard,
                scope=scope,
                mode=mode,
                limit=limit,
                offset=offset,
                cursor=cursor,
            )
        except ValueError as error:
            raise ApiProblem(422, "invalid_ranking_cursor", "The leaderboard cursor is invalid or expired.") from error
        return LeaderboardResponse.model_validate(data)

    @router.get("/player-profiles/me/ratings", response_model=PlayerRatingsSummaryResponse)
    async def get_my_ratings(
        authorization: Annotated[str | None, Header()] = None,
    ) -> PlayerRatingsSummaryResponse:
        player = await authenticated(authorization)
        try:
            data = await asyncio.to_thread(
                rating_service.player_ratings_summary,
                str(player["id"]),
            )
        except ValueError as error:
            raise ApiProblem(404, "player_not_found", str(error)) from error
        return PlayerRatingsSummaryResponse.model_validate(data)

    @router.get("/player-profiles/me/ratings/history", response_model=PlayerRatingHistoryResponse)
    async def get_my_rating_history(
        authorization: Annotated[str | None, Header()] = None,
        scope: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    ) -> PlayerRatingHistoryResponse:
        player = await authenticated(authorization)
        if scope:
            if scope == "global":
                pass
            elif scope.startswith("mode:"):
                mode = scope.partition(":")[2]
                if mode not in ELIGIBLE_MODES:
                    raise ApiProblem(422, "invalid_rating_scope", f"Unsupported rating scope '{scope}'.")
            elif scope in ELIGIBLE_MODES:
                scope = f"mode:{scope}"
            else:
                raise ApiProblem(422, "invalid_rating_scope", f"Unsupported rating scope '{scope}'.")
        try:
            data = await asyncio.to_thread(
                rating_service.player_rating_history,
                str(player["id"]),
                scope=scope,
                limit=limit,
                offset=offset,
            )
        except ValueError as error:
            raise ApiProblem(404, "player_not_found", str(error)) from error
        return PlayerRatingHistoryResponse.model_validate(data)

    app.include_router(router)

"""HTTP APIs for competitive league management, seasons, round-robin fixtures, and standings."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from quizverse.persistence.leagues import (
    LEAGUE_MODES,
    LeagueError,
    LeagueService,
)
from quizverse.persistence.player_identity import (
    PlayerIdentityError,
    PlayerIdentityService,
    ProcessRateLimiter,
)
from quizverse.quiz_api import ApiProblem


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


LeagueMode = Literal["classic", "speed_challenge", "elimination", "power_play"]


class LeagueCreate(StrictRequest):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    mode: LeagueMode
    rules_version: str = Field(default="v1.0", max_length=32)
    configuration: dict[str, Any] = Field(default_factory=dict)


class LeagueUpdate(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)


class SeasonCreate(StrictRequest):
    name: str = Field(min_length=1, max_length=80)
    capacity: int = Field(default=16, ge=2, le=32)
    rules_version: str = Field(default="v1.0", max_length=32)
    configuration: dict[str, Any] = Field(default_factory=dict)


class DisputeCreate(StrictRequest):
    reason: str = Field(min_length=1, max_length=128)
    details: dict[str, Any] = Field(default_factory=dict)


class ForfeitRequest(StrictRequest):
    reason: str = Field(default="player_forfeit", min_length=1, max_length=64)


class MatchLaunchRequest(StrictRequest):
    ticket: str = Field(min_length=1, max_length=128)
    player_name: str | None = Field(default=None, min_length=1, max_length=24)


def _authenticated_header(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiProblem(401, "missing_player_credential", "Authenticate with a Bearer credential token.")
    token = authorization[7:].strip()
    if not token:
        raise ApiProblem(401, "missing_player_credential", "Authenticate with a Bearer credential token.")
    return token


def install_league_api(
    app: FastAPI,
    leagues: LeagueService,
    identities: PlayerIdentityService,
    engine: Any,
) -> None:
    router = APIRouter(prefix="/api/leagues", tags=["leagues"])
    limiter = ProcessRateLimiter(limit=15, window_seconds=60)

    def rate_limit(request: Request, action: str, identity: str = "") -> None:
        address = request.client.host if request.client else "unknown"
        allowed, retry_after = limiter.allow(f"league:{action}:{address}:{identity}")
        if not allowed:
            raise ApiProblem(429, "league_rate_limited", "Too many league requests. Try again shortly.", headers={"Retry-After": str(retry_after)})

    async def authenticate(authorization: str | None) -> dict[str, Any]:
        token = _authenticated_header(authorization)
        try:
            return await asyncio.to_thread(identities.authenticate, token)
        except PlayerIdentityError as error:
            raise ApiProblem(401, "invalid_player_credential", "The player credential is invalid or revoked.") from error

    # -------------------------------------------------------------------------
    # League Endpoints
    # -------------------------------------------------------------------------

    @router.get("")
    async def list_leagues(
        status: str | None = Query(None),
        mode: str | None = Query(None),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        if mode and mode not in LEAGUE_MODES:
            raise ApiProblem(422, "invalid_mode", f"Unsupported league mode '{mode}'.")
        return await asyncio.to_thread(
            leagues.list_leagues,
            status=status,
            mode=mode,
            limit=limit,
            offset=offset,
        )

    @router.post("", status_code=201)
    async def create_league(
        body: LeagueCreate,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        rate_limit(request, "create", str(player["id"]))
        try:
            return await asyncio.to_thread(
                leagues.create_league,
                owner_player_id=str(player["id"]),
                name=body.name,
                description=body.description,
                mode=body.mode,
                rules_version=body.rules_version,
                configuration=body.configuration,
            )
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.get("/{league_id}")
    async def get_league(league_id: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(leagues.get_league, league_id)
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.patch("/{league_id}")
    async def update_league(
        league_id: str,
        body: LeagueUpdate,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                leagues.update_league,
                league_id=league_id,
                actor_id=str(player["id"]),
                name=body.name,
                description=body.description,
            )
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    # -------------------------------------------------------------------------
    # Season Lifecycle Endpoints
    # -------------------------------------------------------------------------

    @router.post("/{league_id}/seasons", status_code=201)
    async def create_season(
        league_id: str,
        body: SeasonCreate,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        rate_limit(request, "create_season", str(player["id"]))
        try:
            return await asyncio.to_thread(
                leagues.create_season,
                league_id=league_id,
                actor_id=str(player["id"]),
                name=body.name,
                capacity=body.capacity,
                rules_version=body.rules_version,
                configuration=body.configuration,
            )
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.get("/{league_id}/seasons/{season_id}")
    async def get_season(league_id: str, season_id: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(leagues.get_season, season_id)
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{league_id}/seasons/{season_id}/open-registration")
    async def open_registration(
        league_id: str,
        season_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                leagues.open_registration,
                season_id=season_id,
                actor_id=str(player["id"]),
            )
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{league_id}/seasons/{season_id}/close-registration")
    async def close_registration(
        league_id: str,
        season_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                leagues.close_registration,
                season_id=season_id,
                actor_id=str(player["id"]),
            )
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{league_id}/seasons/{season_id}/register", status_code=201)
    @router.post("/{league_id}/seasons/{season_id}/registration", status_code=201)
    async def register_player(
        league_id: str,
        season_id: str,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        rate_limit(request, "register", str(player["id"]))
        try:
            return await asyncio.to_thread(
                leagues.register_player,
                season_id=season_id,
                player_id=str(player["id"]),
            )
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{league_id}/seasons/{season_id}/withdraw", status_code=200)
    @router.delete("/{league_id}/seasons/{season_id}/registration", status_code=200)
    async def withdraw_player(
        league_id: str,
        season_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                leagues.withdraw_player,
                season_id=season_id,
                player_id=str(player["id"]),
            )
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{league_id}/seasons/{season_id}/start")
    async def start_season(
        league_id: str,
        season_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                leagues.start_season,
                season_id=season_id,
                actor_id=str(player["id"]),
            )
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{league_id}/seasons/{season_id}/cancel")
    async def cancel_season(
        league_id: str,
        season_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                leagues.cancel_season,
                season_id=season_id,
                actor_id=str(player["id"]),
            )
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    # -------------------------------------------------------------------------
    # Standings & Fixtures
    # -------------------------------------------------------------------------

    @router.get("/{league_id}/seasons/{season_id}/standings")
    async def get_standings(league_id: str, season_id: str) -> list[dict[str, Any]]:
        try:
            return await asyncio.to_thread(leagues.get_standings, season_id)
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.get("/{league_id}/seasons/{season_id}/fixtures")
    async def get_fixtures(
        league_id: str,
        season_id: str,
        round_number: int | None = Query(None),
    ) -> list[dict[str, Any]]:
        try:
            return await asyncio.to_thread(leagues.get_fixtures, season_id, round_number=round_number)
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    # -------------------------------------------------------------------------
    # Fixture Match Launch, Tickets, Forfeits & Disputes
    # -------------------------------------------------------------------------

    @router.get("/{league_id}/seasons/{season_id}/fixtures/{fixture_id}/ticket")
    @router.post("/{league_id}/seasons/{season_id}/fixtures/{fixture_id}/ticket")
    @router.post("/{league_id}/seasons/{season_id}/fixtures/{fixture_id}/admission-ticket")
    async def issue_fixture_ticket(
        league_id: str,
        season_id: str,
        fixture_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                leagues.create_fixture_ticket,
                season_id=season_id,
                fixture_id=fixture_id,
                player_id=str(player["id"]),
            )
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{league_id}/seasons/{season_id}/fixtures/{fixture_id}/launch")
    async def launch_fixture(
        league_id: str,
        season_id: str,
        fixture_id: str,
        body: MatchLaunchRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        player_name = body.player_name or str(player.get("display_name", "Player"))
        try:
            return await leagues.launch_fixture_match(
                engine=engine,
                season_id=season_id,
                fixture_id=fixture_id,
                player_id=str(player["id"]),
                player_name=player_name,
                token=body.ticket,
            )
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{league_id}/seasons/{season_id}/fixtures/{fixture_id}/forfeit")
    async def forfeit_fixture(
        league_id: str,
        season_id: str,
        fixture_id: str,
        body: ForfeitRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                leagues.forfeit_fixture,
                season_id=season_id,
                fixture_id=fixture_id,
                actor_id=str(player["id"]),
                reason=body.reason,
            )
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{league_id}/seasons/{season_id}/fixtures/{fixture_id}/dispute")
    @router.post("/{league_id}/seasons/{season_id}/fixtures/{fixture_id}/disputes")
    async def dispute_fixture(
        league_id: str,
        season_id: str,
        fixture_id: str,
        body: DisputeCreate,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                leagues.dispute_fixture,
                season_id=season_id,
                fixture_id=fixture_id,
                actor_id=str(player["id"]),
                reason=body.reason,
                details=body.details,
            )
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.get("/{league_id}/seasons/{season_id}/audit")
    async def get_audit_trail(league_id: str, season_id: str) -> list[dict[str, Any]]:
        try:
            return await asyncio.to_thread(leagues.get_audit_events, season_id)
        except LeagueError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    app.include_router(router)

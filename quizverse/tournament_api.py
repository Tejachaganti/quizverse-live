"""HTTP APIs for competitive single-elimination tournament management and bracket play."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from quizverse.persistence.player_identity import (
    PlayerIdentityError,
    PlayerIdentityService,
    ProcessRateLimiter,
)
from quizverse.persistence.tournaments import (
    TOURNAMENT_MODES,
    TournamentError,
    TournamentService,
)
from quizverse.quiz_api import ApiProblem


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


TournamentMode = Literal["classic", "speed_challenge", "elimination", "power_play"]


class TournamentCreate(StrictRequest):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    mode: TournamentMode
    capacity: int = Field(default=8, ge=2, le=64)
    rules_version: str = Field(default="v1.0", max_length=32)
    configuration: dict[str, Any] = Field(default_factory=dict)


class TournamentUpdate(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    capacity: int | None = Field(default=None, ge=2, le=64)


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


def install_tournament_api(
    app: FastAPI,
    tournaments: TournamentService,
    identities: PlayerIdentityService,
    engine: Any,
) -> None:
    router = APIRouter(prefix="/api/tournaments", tags=["tournaments"])
    limiter = ProcessRateLimiter(limit=10, window_seconds=60)

    def rate_limit(request: Request, action: str, identity: str = "") -> None:
        address = request.client.host if request.client else "unknown"
        allowed, retry_after = limiter.allow(f"tourn:{action}:{address}:{identity}")
        if not allowed:
            raise ApiProblem(429, "tournament_rate_limited", "Too many tournament requests. Try again shortly.", headers={"Retry-After": str(retry_after)})

    async def authenticate(authorization: str | None) -> dict[str, Any]:
        token = _authenticated_header(authorization)
        try:
            return await asyncio.to_thread(identities.authenticate, token)
        except PlayerIdentityError as error:
            raise ApiProblem(401, "invalid_player_credential", "The player credential is invalid or revoked.") from error

    @router.get("")
    async def list_tournaments(
        status: str | None = Query(None),
        mode: str | None = Query(None),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        if mode and mode not in TOURNAMENT_MODES:
            raise ApiProblem(422, "invalid_mode", f"Unsupported tournament mode '{mode}'.")
        return await asyncio.to_thread(
            tournaments.list_tournaments,
            status=status,
            mode=mode,
            limit=limit,
            offset=offset,
        )

    @router.post("", status_code=201)
    async def create_tournament(
        body: TournamentCreate,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        rate_limit(request, "create", str(player["id"]))
        try:
            return await asyncio.to_thread(
                tournaments.create_tournament,
                owner_player_id=str(player["id"]),
                name=body.name,
                description=body.description,
                mode=body.mode,
                capacity=body.capacity,
                rules_version=body.rules_version,
                configuration=body.configuration,
            )
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.get("/{tournament_id}")
    async def get_tournament(tournament_id: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(tournaments.get_tournament, tournament_id)
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.patch("/{tournament_id}")
    async def update_tournament(
        tournament_id: str,
        body: TournamentUpdate,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                tournaments.update_tournament,
                tournament_id=tournament_id,
                actor_id=str(player["id"]),
                name=body.name,
                description=body.description,
                capacity=body.capacity,
            )
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{tournament_id}/open-registration")
    async def open_registration(
        tournament_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                tournaments.open_registration,
                tournament_id=tournament_id,
                actor_id=str(player["id"]),
            )
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{tournament_id}/close-registration")
    async def close_registration(
        tournament_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                tournaments.close_registration,
                tournament_id=tournament_id,
                actor_id=str(player["id"]),
            )
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{tournament_id}/registration", status_code=201)
    async def register_player(
        tournament_id: str,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        rate_limit(request, "register", str(player["id"]))
        try:
            return await asyncio.to_thread(
                tournaments.register_player,
                tournament_id=tournament_id,
                player_id=str(player["id"]),
            )
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.delete("/{tournament_id}/registration", status_code=200)
    async def withdraw_player(
        tournament_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                tournaments.withdraw_player,
                tournament_id=tournament_id,
                player_id=str(player["id"]),
            )
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{tournament_id}/start")
    async def start_tournament(
        tournament_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                tournaments.start_tournament,
                tournament_id=tournament_id,
                actor_id=str(player["id"]),
            )
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{tournament_id}/cancel")
    async def cancel_tournament(
        tournament_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                tournaments.cancel_tournament,
                tournament_id=tournament_id,
                actor_id=str(player["id"]),
            )
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.get("/{tournament_id}/bracket")
    async def get_bracket(tournament_id: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(tournaments.get_bracket, tournament_id)
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.get("/{tournament_id}/matches")
    async def list_matches(
        tournament_id: str,
        round_id: str | None = Query(None),
    ) -> list[dict[str, Any]]:
        try:
            return await asyncio.to_thread(tournaments.list_matches, tournament_id, round_id=round_id)
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.get("/{tournament_id}/matches/{match_id}")
    async def get_match(tournament_id: str, match_id: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(tournaments.get_match, tournament_id, match_id)
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{tournament_id}/matches/{match_id}/admission-ticket")
    async def issue_admission_ticket(
        tournament_id: str,
        match_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                tournaments.issue_fixture_admission_ticket,
                tournament_id=tournament_id,
                match_id=match_id,
                player_id=str(player["id"]),
            )
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{tournament_id}/matches/{match_id}/launch")
    async def launch_match(
        tournament_id: str,
        match_id: str,
        body: MatchLaunchRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        player_name = body.player_name or str(player.get("display_name", "Player"))
        try:
            return await tournaments.launch_or_join_fixture(
                engine=engine,
                tournament_id=tournament_id,
                match_id=match_id,
                player_id=str(player["id"]),
                player_name=player_name,
                token=body.ticket,
            )
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{tournament_id}/matches/{match_id}/forfeit")
    async def forfeit_match(
        tournament_id: str,
        match_id: str,
        body: ForfeitRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                tournaments.forfeit_match,
                tournament_id=tournament_id,
                match_id=match_id,
                forfeiting_player_id=str(player["id"]),
                reason=body.reason,
            )
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.post("/{tournament_id}/matches/{match_id}/disputes")
    async def file_dispute(
        tournament_id: str,
        match_id: str,
        body: DisputeCreate,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player = await authenticate(authorization)
        try:
            return await asyncio.to_thread(
                tournaments.file_dispute,
                tournament_id=tournament_id,
                match_id=match_id,
                player_id=str(player["id"]),
                reason=body.reason,
                details=body.details,
            )
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    @router.get("/{tournament_id}/audit")
    async def get_audit_trail(tournament_id: str) -> list[dict[str, Any]]:
        try:
            return await asyncio.to_thread(tournaments.get_audit_trail, tournament_id)
        except TournamentError as error:
            raise ApiProblem(error.status_code, error.code, error.message) from error

    app.include_router(router)

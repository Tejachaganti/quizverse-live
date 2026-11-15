"""Comprehensive unit and integration tests for Stage 9.3 Casual Direct Rematch Challenges."""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from quizverse.engine import GameEngine, Phase
from quizverse.persistence.db import Database
from quizverse.persistence.invitations import (
    InvitationError,
    RematchService,
)
from quizverse.persistence.models import (
    CompetitiveMatch,
    MatchParticipant,
    PlayerBlock,
    PlayerProfile,
    RematchChallenge,
    utc_now,
)
from quizverse.persistence.player_identity import PlayerIdentityService
from quizverse.persistence.ratings import RatingService
from quizverse.persistence.social import BlockService, FriendService
from quizverse.presence import (
    LocalMemoryPresenceBroker,
    PresenceService,
    PresenceTicketService,
)
from quizverse.realtime import create_app


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'rematches-test.sqlite3').as_posix()}"
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(Config(str(project_root / "alembic.ini")), "head")
    finally:
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url

    db = Database(url)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def identities(test_db):
    return PlayerIdentityService(test_db)


@pytest.fixture
def ratings(test_db):
    return RatingService(test_db)


@pytest.fixture
def block_service(test_db):
    return BlockService(test_db)


@pytest.fixture
def friend_service(test_db, block_service, ratings):
    return FriendService(test_db, block_service, ratings)


@pytest.fixture
def presence_broker():
    return LocalMemoryPresenceBroker()


@pytest.fixture
def presence_service(test_db, friend_service, block_service, presence_broker):
    return PresenceService(test_db, friend_service, block_service, PresenceTicketService(), presence_broker)


@pytest.fixture
def engine():
    return GameEngine()


@pytest.fixture
def rematch_service(test_db, friend_service, block_service, presence_service):
    return RematchService(
        database=test_db,
        friend_service=friend_service,
        block_service=block_service,
        presence_service=presence_service,
    )


def create_player(client: TestClient, name: str) -> tuple[str, str]:
    res = client.post("/api/player-profiles", json={"display_name": name})
    assert res.status_code == 201, res.text
    data = res.json()
    return data["player"]["id"], data["credential"]


def create_completed_match(
    database: Database,
    p1_id: str,
    p2_id: str,
    *,
    mode: str = "classic",
    status: str = "completed",
    result_context: dict | None = None,
) -> str:
    match_id = secrets.token_hex(16)
    now = utc_now()
    with database.sessions.begin() as session:
        match = CompetitiveMatch(
            id=match_id,
            engine_match_id=f"eng_{match_id}",
            mode=mode,
            status=status,
            result_kind="individual",
            rules_version="v1.0",
            started_at=now - timedelta(minutes=5),
            completed_at=now,
            question_set_hash="a" * 64,
            result_digest="b" * 64,
            result_context=result_context or {},
            is_rated=True,
        )
        session.add(match)
        session.flush()

        mp1 = MatchParticipant(
            id=secrets.token_hex(16),
            match_id=match_id,
            player_id=p1_id,
            seat_index=0,
            final_score=100,
            correct_count=8,
            answer_count=10,
            placement=1,
            outcome="win",
            forfeited=False,
        )
        mp2 = MatchParticipant(
            id=secrets.token_hex(16),
            match_id=match_id,
            player_id=p2_id,
            seat_index=1,
            final_score=80,
            correct_count=6,
            answer_count=10,
            placement=2,
            outcome="loss",
            forfeited=False,
        )
        session.add(mp1)
        session.add(mp2)
    return match_id


# ---------------------------------------------------------------------------
# 1. Rematch Creation & Validation Tests
# ---------------------------------------------------------------------------

def test_create_rematch_success(test_db, identities, rematch_service):
    async def _run():
        p1 = identities.create("RematchP1")
        p2 = identities.create("RematchP2")
        match_id = create_completed_match(test_db, p1.player_id, p2.player_id, mode="classic")

        chal = await rematch_service.create_challenge(
            challenger_id=p1.player_id,
            opponent_id=p2.player_id,
            original_match_id=match_id,
        )
        assert chal["challenge_id"] is not None
        assert chal["mode"] == "classic"
        assert chal["status"] == "pending"

    asyncio.run(_run())


def test_create_rematch_cannot_challenge_self(test_db, identities, rematch_service):
    async def _run():
        p1 = identities.create("SelfChallenger1")
        p2 = identities.create("SelfChallenger2")
        match_id = create_completed_match(test_db, p1.player_id, p2.player_id)

        with pytest.raises(InvitationError) as exc:
            await rematch_service.create_challenge(
                challenger_id=p1.player_id,
                opponent_id=p1.player_id,
                original_match_id=match_id,
            )
        assert exc.value.code == "cannot_challenge_self"
        assert exc.value.status_code == 400

    asyncio.run(_run())


def test_create_rematch_match_not_found(test_db, identities, rematch_service):
    async def _run():
        p1 = identities.create("NotFoundP1")
        p2 = identities.create("NotFoundP2")

        with pytest.raises(InvitationError) as exc:
            await rematch_service.create_challenge(
                challenger_id=p1.player_id,
                opponent_id=p2.player_id,
                original_match_id="nonexistent_match_id",
            )
        assert exc.value.code == "match_not_found"
        assert exc.value.status_code == 404

    asyncio.run(_run())


def test_create_rematch_tournament_match_ineligible(test_db, identities, rematch_service):
    async def _run():
        p1 = identities.create("TournP1")
        p2 = identities.create("TournP2")
        match_id = create_completed_match(
            test_db, p1.player_id, p2.player_id,
            result_context={"tournament_id": "tourn-123", "tournament_match_id": "tm-456"}
        )

        with pytest.raises(InvitationError) as exc:
            await rematch_service.create_challenge(
                challenger_id=p1.player_id,
                opponent_id=p2.player_id,
                original_match_id=match_id,
            )
        assert exc.value.code == "tournament_rematch_ineligible"
        assert exc.value.status_code == 400

    asyncio.run(_run())


def test_create_rematch_league_fixture_ineligible(test_db, identities, rematch_service):
    async def _run():
        p1 = identities.create("LeagueP1")
        p2 = identities.create("LeagueP2")
        match_id = create_completed_match(
            test_db, p1.player_id, p2.player_id,
            result_context={"league_id": "league-123", "league_fixture_id": "fix-456"}
        )

        with pytest.raises(InvitationError) as exc:
            await rematch_service.create_challenge(
                challenger_id=p1.player_id,
                opponent_id=p2.player_id,
                original_match_id=match_id,
            )
        assert exc.value.code == "league_rematch_ineligible"
        assert exc.value.status_code == 400

    asyncio.run(_run())


def test_create_rematch_non_participant_rejected(test_db, identities, rematch_service):
    async def _run():
        p1 = identities.create("PartP1")
        p2 = identities.create("PartP2")
        p3 = identities.create("OutsiderP3")
        match_id = create_completed_match(test_db, p1.player_id, p2.player_id)

        with pytest.raises(InvitationError) as exc:
            await rematch_service.create_challenge(
                challenger_id=p3.player_id,
                opponent_id=p2.player_id,
                original_match_id=match_id,
            )
        assert exc.value.code == "unrelated_match_participant"
        assert exc.value.status_code == 400

    asyncio.run(_run())


def test_create_rematch_duplicate_and_limits(test_db, identities, rematch_service):
    async def _run():
        p1 = identities.create("LimitP1")
        p2 = identities.create("LimitP2")
        m1 = create_completed_match(test_db, p1.player_id, p2.player_id)

        await rematch_service.create_challenge(p1.player_id, p2.player_id, m1)

        # Duplicate pending challenge
        with pytest.raises(InvitationError) as exc:
            await rematch_service.create_challenge(p1.player_id, p2.player_id, m1)
        assert exc.value.code == "duplicate_challenge"
        assert exc.value.status_code == 409

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 2. Acceptance, Room Creation, and Authorization Tests
# ---------------------------------------------------------------------------

def test_accept_rematch_creates_fresh_casual_room(test_db, identities, engine, rematch_service):
    async def _run():
        p1 = identities.create("AcceptChallenger")
        p2 = identities.create("AcceptOpponent")
        m1 = create_completed_match(test_db, p1.player_id, p2.player_id, mode="classic")

        chal = await rematch_service.create_challenge(p1.player_id, p2.player_id, m1)
        chal_id = chal["challenge_id"]

        accepted = await rematch_service.accept_challenge(chal_id, p2.player_id, engine)
        room_code = accepted["room_code"]
        assert room_code is not None
        assert len(room_code) == 6

        # Verify room state in engine
        async with engine._lock:
            room = engine._rooms.get(room_code)
            assert room is not None
            assert room.access_policy == "invite_only"
            # Both players must be pre-authorized
            assert p1.player_id in room.authorized_profile_ids
            assert p2.player_id in room.authorized_profile_ids
            # Zero tournament/league linkage
            assert "tournament_id" not in room.selection
            assert "league_id" not in room.selection
            assert room.selection.get("is_rematch") is True

    asyncio.run(_run())


def test_decline_and_cancel_rematch(test_db, identities, rematch_service):
    async def _run():
        p1 = identities.create("DecP1")
        p2 = identities.create("DecP2")
        m1 = create_completed_match(test_db, p1.player_id, p2.player_id)
        m2 = create_completed_match(test_db, p1.player_id, p2.player_id)

        c1 = await rematch_service.create_challenge(p1.player_id, p2.player_id, m1)
        c2 = await rematch_service.create_challenge(p1.player_id, p2.player_id, m2)

        # Opponent declines c1
        dec = rematch_service.decline_challenge(c1["challenge_id"], p2.player_id)
        assert dec["status"] == "declined"

        # Challenger cancels c2
        canc = rematch_service.cancel_challenge(c2["challenge_id"], p1.player_id)
        assert canc["status"] == "cancelled"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. REST API Flow Tests
# ---------------------------------------------------------------------------

def test_rematches_api_flow(test_db):
    app = create_app(database=test_db)
    with TestClient(app) as client:
        p1_id, p1_cred = create_player(client, "APIRematchP1")
        p2_id, p2_cred = create_player(client, "APIRematchP2")
        h1 = {"Authorization": f"Bearer {p1_cred}"}
        h2 = {"Authorization": f"Bearer {p2_cred}"}

        match_id = create_completed_match(test_db, p1_id, p2_id, mode="classic")

        # P1 issues challenge
        create_res = client.post(
            "/api/social/rematches",
            json={"opponent_id": p2_id, "original_match_id": match_id},
            headers=h1,
        )
        assert create_res.status_code == 201, create_res.text
        chal_id = create_res.json()["challenge_id"]

        # Check incoming on P2
        inc_res = client.get("/api/social/rematches/incoming", headers=h2)
        assert inc_res.status_code == 200
        assert len(inc_res.json()["incoming_challenges"]) == 1

        # Check outgoing on P1
        out_res = client.get("/api/social/rematches/outgoing", headers=h1)
        assert out_res.status_code == 200
        assert len(out_res.json()["outgoing_challenges"]) == 1

        # P2 accepts challenge
        acc_res = client.post(f"/api/social/rematches/{chal_id}/accept", headers=h2)
        assert acc_res.status_code == 200, acc_res.text
        assert "room_code" in acc_res.json()
        assert "token" in acc_res.json()

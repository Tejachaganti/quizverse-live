"""Comprehensive unit and integration tests for Stage 9.3 Room Invitations and Secure Admission."""

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

from quizverse.engine import GameEngine, GameError, Phase, Room
from quizverse.persistence.db import Database
from quizverse.persistence.invitations import (
    InvitationError,
    InvitationService,
    _token_hash,
)
from quizverse.persistence.models import (
    PlayerBlock,
    PlayerProfile,
    RoomInvitation,
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
    url = f"sqlite:///{(tmp_path / 'invitations-test.sqlite3').as_posix()}"
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
def invitation_service(test_db, friend_service, block_service, presence_service):
    return InvitationService(
        database=test_db,
        friend_service=friend_service,
        block_service=block_service,
        presence_service=presence_service,
    )


def make_friends(friend_service: FriendService, p1_id: str, p2_id: str):
    req = friend_service.send_request(p1_id, p2_id)
    friend_service.accept_request(req["request_id"], p2_id)


def create_player(client: TestClient, name: str) -> tuple[str, str]:
    res = client.post("/api/player-profiles", json={"display_name": name})
    assert res.status_code == 201, res.text
    data = res.json()
    return data["player"]["id"], data["credential"]


# ---------------------------------------------------------------------------
# 1. Invitation Creation & Validation Tests
# ---------------------------------------------------------------------------

def test_create_invitation_success(test_db, identities, friend_service, engine, invitation_service):
    async def _run():
        p1 = identities.create("PlayerHost")
        p2 = identities.create("PlayerGuest")
        make_friends(friend_service, p1.player_id, p2.player_id)

        created = await engine.create_room("PlayerHost", mode="classic", profile_id=p1.player_id, access_policy="invite_only")
        room_code = created["state"]["code"]

        inv = await invitation_service.create_invitation(
            inviter_id=p1.player_id,
            invitee_id=p2.player_id,
            room_code=room_code,
            engine=engine,
        )
        assert inv["room_code"] == room_code
        assert inv["status"] == "pending"
        assert inv["invitee_id"] == p2.player_id
        assert inv["inviter_id"] == p1.player_id

    asyncio.run(_run())


def test_create_invitation_cannot_invite_self(test_db, identities, engine, invitation_service):
    async def _run():
        p1 = identities.create("SoloHost")
        created = await engine.create_room("SoloHost", mode="classic", profile_id=p1.player_id)
        room_code = created["state"]["code"]

        with pytest.raises(InvitationError) as exc:
            await invitation_service.create_invitation(
                inviter_id=p1.player_id,
                invitee_id=p1.player_id,
                room_code=room_code,
                engine=engine,
            )
        assert exc.value.code == "cannot_invite_self"
        assert exc.value.status_code == 400

    asyncio.run(_run())


def test_create_invitation_not_friends(test_db, identities, engine, invitation_service):
    async def _run():
        p1 = identities.create("StrangerA")
        p2 = identities.create("StrangerB")
        created = await engine.create_room("StrangerA", mode="classic", profile_id=p1.player_id)
        room_code = created["state"]["code"]

        with pytest.raises(InvitationError) as exc:
            await invitation_service.create_invitation(
                inviter_id=p1.player_id,
                invitee_id=p2.player_id,
                room_code=room_code,
                engine=engine,
            )
        assert exc.value.code == "not_friends"
        assert exc.value.status_code == 403

    asyncio.run(_run())


def test_create_invitation_blocked_player(test_db, identities, friend_service, block_service, engine, invitation_service):
    async def _run():
        p1 = identities.create("BlockerA")
        p2 = identities.create("BlockedB")
        make_friends(friend_service, p1.player_id, p2.player_id)
        block_service.block_player(p1.player_id, p2.player_id)

        created = await engine.create_room("BlockerA", mode="classic", profile_id=p1.player_id)
        room_code = created["state"]["code"]

        with pytest.raises(InvitationError) as exc:
            await invitation_service.create_invitation(
                inviter_id=p1.player_id,
                invitee_id=p2.player_id,
                room_code=room_code,
                engine=engine,
            )
        assert exc.value.code == "blocked_player"
        assert exc.value.status_code == 403

    asyncio.run(_run())


def test_create_invitation_duplicate_pending(test_db, identities, friend_service, engine, invitation_service):
    async def _run():
        p1 = identities.create("DupHost")
        p2 = identities.create("DupGuest")
        make_friends(friend_service, p1.player_id, p2.player_id)

        created = await engine.create_room("DupHost", mode="classic", profile_id=p1.player_id)
        room_code = created["state"]["code"]

        await invitation_service.create_invitation(
            inviter_id=p1.player_id,
            invitee_id=p2.player_id,
            room_code=room_code,
            engine=engine,
        )

        with pytest.raises(InvitationError) as exc:
            await invitation_service.create_invitation(
                inviter_id=p1.player_id,
                invitee_id=p2.player_id,
                room_code=room_code,
                engine=engine,
            )
        assert exc.value.code == "duplicate_invitation"
        assert exc.value.status_code == 409

    asyncio.run(_run())


def test_create_invitation_max_pending_exceeded(test_db, identities, friend_service, engine, invitation_service):
    async def _run():
        p1 = identities.create("PopularHost")
        created = await engine.create_room("PopularHost", mode="classic", profile_id=p1.player_id)
        room_code = created["state"]["code"]

        guests = [identities.create(f"Friend{i}") for i in range(4)]
        for g in guests:
            make_friends(friend_service, p1.player_id, g.player_id)

        for i in range(3):
            await invitation_service.create_invitation(
                inviter_id=p1.player_id,
                invitee_id=guests[i].player_id,
                room_code=room_code,
                engine=engine,
            )

        with pytest.raises(InvitationError) as exc:
            await invitation_service.create_invitation(
                inviter_id=p1.player_id,
                invitee_id=guests[3].player_id,
                room_code=room_code,
                engine=engine,
            )
        assert exc.value.code == "max_pending_invitations"
        assert exc.value.status_code == 429

    asyncio.run(_run())


def test_create_invitation_room_not_waiting_or_full(test_db, identities, friend_service, engine, invitation_service):
    async def _run():
        p1 = identities.create("GameStartedHost")
        p2 = identities.create("LateGuest")
        make_friends(friend_service, p1.player_id, p2.player_id)

        created = await engine.create_room("GameStartedHost", mode="classic", profile_id=p1.player_id)
        room_code = created["state"]["code"]
        async with engine._lock:
            engine._rooms[room_code].phase = Phase.QUESTION

        with pytest.raises(InvitationError) as exc:
            await invitation_service.create_invitation(
                inviter_id=p1.player_id,
                invitee_id=p2.player_id,
                room_code=room_code,
                engine=engine,
            )
        assert exc.value.code == "room_not_waiting"
        assert exc.value.status_code == 400

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 2. Acceptance, Ticket Issuance, and Secure Admission
# ---------------------------------------------------------------------------

def test_accept_and_admit_success(test_db, identities, friend_service, engine, invitation_service):
    async def _run():
        p1 = identities.create("PrivateHost")
        p2 = identities.create("PrivateGuest")
        make_friends(friend_service, p1.player_id, p2.player_id)

        created = await engine.create_room("PrivateHost", mode="classic", profile_id=p1.player_id, access_policy="invite_only")
        room_code = created["state"]["code"]

        inv = await invitation_service.create_invitation(
            inviter_id=p1.player_id,
            invitee_id=p2.player_id,
            room_code=room_code,
            engine=engine,
        )
        inv_id = inv["invitation_id"]

        accepted = await invitation_service.accept_invitation(
            invitation_id=inv_id,
            invitee_id=p2.player_id,
            engine=engine,
        )
        ticket = accepted["admission_ticket"]
        assert ticket is not None
        assert len(ticket) >= 32

        joined = await invitation_service.admit_to_room(
            invitation_id=inv_id,
            invitee_id=p2.player_id,
            admission_ticket=ticket,
            player_name="PrivateGuest",
            engine=engine,
        )
        assert joined["state"]["code"] == room_code
        assert any(p["name"] == "PrivateGuest" for p in joined["state"]["players"])

    asyncio.run(_run())


def test_ticket_replay_prevention(test_db, identities, friend_service, engine, invitation_service):
    async def _run():
        p1 = identities.create("ReplayHost")
        p2 = identities.create("ReplayGuest")
        make_friends(friend_service, p1.player_id, p2.player_id)

        created = await engine.create_room("ReplayHost", mode="classic", profile_id=p1.player_id, access_policy="invite_only")
        room_code = created["state"]["code"]

        inv = await invitation_service.create_invitation(
            inviter_id=p1.player_id,
            invitee_id=p2.player_id,
            room_code=room_code,
            engine=engine,
        )
        accepted = await invitation_service.accept_invitation(inv["invitation_id"], p2.player_id, engine)
        ticket = accepted["admission_ticket"]

        # First admission succeeds
        await invitation_service.admit_to_room(inv["invitation_id"], p2.player_id, ticket, "ReplayGuest", engine)

        # Second admission attempt must fail (replay)
        with pytest.raises(InvitationError) as exc:
            await invitation_service.admit_to_room(inv["invitation_id"], p2.player_id, ticket, "ReplayGuest2", engine)
        assert exc.value.code == "ticket_already_used"
        assert exc.value.status_code == 401

    asyncio.run(_run())


def test_private_room_casual_code_bypass_blocked(test_db, identities, friend_service, engine, invitation_service):
    async def _run():
        p1 = identities.create("SecureHost")
        p2 = identities.create("CasualAttacker")
        created = await engine.create_room("SecureHost", mode="classic", profile_id=p1.player_id, access_policy="invite_only")
        room_code = created["state"]["code"]

        with pytest.raises(GameError) as exc:
            await engine.join_room(room_code, "CasualAttacker", profile_id=p2.player_id)
        assert exc.value.code == "invitation_required"

    asyncio.run(_run())


def test_official_fixture_casual_join_blocked(test_db, identities, engine):
    async def _run():
        created = await engine.create_room(
            "TournamentPlayer1",
            mode="classic",
            selection={"tournament_id": "tourn-123", "tournament_match_id": "tm-456"},
            access_policy="official_fixture",
        )
        room_code = created["state"]["code"]

        with pytest.raises(GameError) as exc:
            await engine.join_room(room_code, "CasualInfiltrator")
        assert exc.value.code == "official_fixture_restricted"

    asyncio.run(_run())


def test_decline_and_cancel_invitation(test_db, identities, friend_service, engine, invitation_service):
    async def _run():
        p1 = identities.create("LifecycleHost")
        p2 = identities.create("LifecycleGuest")
        p3 = identities.create("LifecycleGuest2")
        make_friends(friend_service, p1.player_id, p2.player_id)
        make_friends(friend_service, p1.player_id, p3.player_id)

        created = await engine.create_room("LifecycleHost", mode="classic", profile_id=p1.player_id)
        room_code = created["state"]["code"]

        inv1 = await invitation_service.create_invitation(p1.player_id, p2.player_id, room_code, engine)
        inv2 = await invitation_service.create_invitation(p1.player_id, p3.player_id, room_code, engine)

        dec = invitation_service.decline_invitation(inv1["invitation_id"], p2.player_id)
        assert dec["status"] == "declined"

        canc = invitation_service.cancel_invitation(inv2["invitation_id"], p1.player_id)
        assert canc["status"] == "cancelled"

    asyncio.run(_run())


def test_severed_friendship_cancels_pending_invitations(test_db, identities, friend_service, engine, invitation_service):
    async def _run():
        p1 = identities.create("FriendA")
        p2 = identities.create("FriendB")
        make_friends(friend_service, p1.player_id, p2.player_id)

        created = await engine.create_room("FriendA", mode="classic", profile_id=p1.player_id)
        room_code = created["state"]["code"]

        inv = await invitation_service.create_invitation(p1.player_id, p2.player_id, room_code, engine)
        assert inv["status"] == "pending"

        invitation_service.cancel_pending_between_players(p1.player_id, p2.player_id)

        with test_db.sessions.begin() as session:
            stored = session.get(RoomInvitation, inv["invitation_id"])
            assert stored.status == "cancelled"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. REST API & Admission Tests
# ---------------------------------------------------------------------------

def test_invitations_api_flow(test_db):
    app = create_app(database=test_db)
    with TestClient(app) as client:
        p1_id, p1_cred = create_player(client, "APIHost")
        p2_id, p2_cred = create_player(client, "APIGuest")
        h1 = {"Authorization": f"Bearer {p1_cred}"}
        h2 = {"Authorization": f"Bearer {p2_cred}"}

        # Make friends
        req = client.post("/api/friends/requests", json={"recipient_id": p2_id}, headers=h1).json()
        client.post(f"/api/friends/requests/{req['request_id']}/accept", headers=h2)

        # Host creates room in engine
        engine = app.state.engine
        joined_host = asyncio.run(engine.create_room("APIHost", mode="classic", profile_id=p1_id, access_policy="invite_only"))
        room_code = joined_host["state"]["code"]

        # Send invitation
        inv_res = client.post("/api/social/invitations", json={"invitee_id": p2_id, "room_code": room_code}, headers=h1)
        assert inv_res.status_code == 201, inv_res.text
        inv_id = inv_res.json()["invitation_id"]

        # Check incoming on guest
        inc_res = client.get("/api/social/invitations/incoming", headers=h2)
        assert inc_res.status_code == 200
        assert len(inc_res.json()["incoming_invitations"]) == 1

        # Check outgoing on host
        out_res = client.get("/api/social/invitations/outgoing", headers=h1)
        assert out_res.status_code == 200
        assert len(out_res.json()["outgoing_invitations"]) == 1

        # Guest accepts
        acc_res = client.post(f"/api/social/invitations/{inv_id}/accept", headers=h2)
        assert acc_res.status_code == 200
        ticket = acc_res.json()["admission_ticket"]

        # Guest joins
        join_res = client.post(
            f"/api/social/invitations/{inv_id}/join",
            json={"admission_ticket": ticket, "player_name": "APIGuest"},
            headers=h2,
        )
        assert join_res.status_code == 200
        assert join_res.json()["state"]["code"] == room_code

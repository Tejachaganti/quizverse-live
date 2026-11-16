"""Unit, integration, and privacy tests for Social Intelligence and activity streams."""

from __future__ import annotations

import asyncio
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from quizverse.persistence.db import Database
from quizverse.persistence.invitations import InvitationService
from quizverse.persistence.messaging import MessagingService
from quizverse.persistence.models import (
    Friendship,
    PlayerBlock,
    PlayerProfile,
    Tournament,
    TournamentParticipant,
    utc_now,
)
from quizverse.persistence.notifications import NotificationService, SocialIntelligenceService
from quizverse.persistence.player_identity import PlayerIdentityService
from quizverse.persistence.social import BlockService, FriendService
from quizverse.realtime import create_app


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'social-intel-test.sqlite3').as_posix()}"
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "quizverse" / "migrations"))
    try:
        command.upgrade(config, "head")
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
def identity_service(test_db: Database) -> PlayerIdentityService:
    return PlayerIdentityService(test_db)


@pytest.fixture
def block_service(test_db: Database) -> BlockService:
    return BlockService(test_db)


@pytest.fixture
def friend_service(test_db: Database, block_service: BlockService) -> FriendService:
    return FriendService(test_db, block_service)


@pytest.fixture
def social_intel_service(
    test_db: Database, friend_service: FriendService, block_service: BlockService
) -> SocialIntelligenceService:
    return SocialIntelligenceService(test_db, friend_service, block_service)


@pytest.fixture
def notification_service(test_db: Database, block_service: BlockService) -> NotificationService:
    return NotificationService(test_db, block_service)


@pytest.fixture
def invitation_service(
    test_db: Database, friend_service: FriendService, block_service: BlockService, notification_service: NotificationService
) -> InvitationService:
    svc = InvitationService(test_db, friend_service, block_service)
    svc.notification_service = notification_service
    return svc


@pytest.fixture
def messaging_service(
    test_db: Database, friend_service: FriendService, block_service: BlockService, notification_service: NotificationService
) -> MessagingService:
    svc = MessagingService(test_db, friend_service, block_service)
    svc.notification_service = notification_service
    return svc


def _create_friendship(db: Database, p1_id: str, p2_id: str) -> None:
    pair1, pair2 = (p1_id, p2_id) if p1_id < p2_id else (p2_id, p1_id)
    with db.sessions.begin() as session:
        f = Friendship(
            id=secrets.token_hex(16),
            player_one_id=pair1,
            player_two_id=pair2,
            created_at=utc_now(),
        )
        session.add(f)


def test_social_overview_aggregation(
    test_db: Database,
    identity_service: PlayerIdentityService,
    friend_service: FriendService,
    invitation_service: InvitationService,
    messaging_service: MessagingService,
    social_intel_service: SocialIntelligenceService,
):
    p1 = identity_service.create("Alice")
    p2 = identity_service.create("Bob")
    p3 = identity_service.create("Charlie")

    # Alice and Bob are friends
    _create_friendship(test_db, p1.player_id, p2.player_id)

    # Charlie sends friend request to Alice
    friend_service.send_request(p3.player_id, p1.player_id)

    # Bob invites Alice to a room
    class FakeEngine:
        async def get_room_metadata(self, room_code: str):
            return {
                "is_waiting": True,
                "is_full": False,
                "player_count": 1,
                "max_players": 4,
                "game_mode": "classic",
                "mode": "classic",
                "member_profile_ids": [p2.player_id],
                "host_profile_id": p2.player_id,
            }

    asyncio.run(invitation_service.create_invitation(
        inviter_id=p2.player_id,
        invitee_id=p1.player_id,
        room_code="ROOM88",
        engine=FakeEngine(),
    ))

    # Bob sends Alice a DM
    conv = messaging_service.get_or_create_conversation(p2.player_id, p1.player_id)
    messaging_service.send_message(conv["id"], p2.player_id, "Hello Alice!")

    overview = social_intel_service.get_social_overview(p1.player_id)

    assert overview["friend_count"] == 1
    assert overview["pending_requests_count"] == 1
    assert overview["pending_invitations_count"] == 1
    assert overview["unread_messages_count"] == 1


def test_bidirectional_block_enforcement_in_social_overview(
    test_db: Database,
    identity_service: PlayerIdentityService,
    block_service: BlockService,
    social_intel_service: SocialIntelligenceService,
):
    p1 = identity_service.create("Alice")
    p2 = identity_service.create("Bob")
    p3 = identity_service.create("Charlie")

    _create_friendship(test_db, p1.player_id, p2.player_id)
    _create_friendship(test_db, p2.player_id, p3.player_id)

    # Bob has recent friendship with Charlie
    overview_before = social_intel_service.get_social_overview(p1.player_id)
    assert any("Bob" in act["description"] for act in overview_before["recent_activity"])

    # Alice blocks Bob
    block_service.block_player(p1.player_id, p2.player_id)

    # Now Bob must NOT appear in Alice's activity
    overview_after = social_intel_service.get_social_overview(p1.player_id)
    assert all("Bob" not in act["description"] for act in overview_after["recent_activity"])


def test_privacy_in_recent_activity(
    test_db: Database,
    identity_service: PlayerIdentityService,
    social_intel_service: SocialIntelligenceService,
):
    p1 = identity_service.create("Alice")
    p2 = identity_service.create("Bob")

    _create_friendship(test_db, p1.player_id, p2.player_id)

    overview = social_intel_service.get_social_overview(p1.player_id)
    for act in overview["recent_activity"]:
        # Ensure no private tokens, codes, or credentials are exposed
        assert "token" not in act["description"].lower()
        assert "password" not in act["description"].lower()
        assert "credential" not in act["description"].lower()


def test_social_overview_rest_api_endpoint(test_db: Database):
    app = create_app(database=test_db)
    with TestClient(app) as client:
        # 401 when unauthorized
        unauth = client.get("/api/notifications/social-overview")
        assert unauth.status_code == 401

        res = client.post("/api/player-profiles", json={"display_name": "OverviewPlayer"}).json()
        player_id, cred = res["player"]["id"], res["credential"]
        h = {"Authorization": f"Bearer {cred}"}

        resp = client.get("/api/notifications/social-overview", headers=h)
        assert resp.status_code == 200
        data = resp.json()
        assert "friend_count" in data
        assert "online_friends_count" in data
        assert "pending_requests_count" in data
        assert "pending_invitations_count" in data
        assert "recent_activity" in data

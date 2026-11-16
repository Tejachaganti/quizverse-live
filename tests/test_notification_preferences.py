"""Unit and integration tests for notification preferences and category suppression."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from quizverse.persistence.db import Database
from quizverse.persistence.invitations import InvitationService
from quizverse.persistence.messaging import MessagingService
from quizverse.persistence.models import NotificationPreference
from quizverse.persistence.notifications import NotificationService
from quizverse.persistence.player_identity import PlayerIdentityService
from quizverse.persistence.social import BlockService, FriendService
from quizverse.realtime import create_app


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'preferences-test.sqlite3').as_posix()}"
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


def test_default_preferences_initialization(
    identity_service: PlayerIdentityService, notification_service: NotificationService
):
    p = identity_service.create("Alice")
    prefs = notification_service.get_preferences(p.player_id)

    assert prefs["player_id"] == p.player_id
    assert prefs["friend_requests"] is True
    assert prefs["friendship_updates"] is True
    assert prefs["friend_online"] is False
    assert prefs["room_invitations"] is True
    assert prefs["rematch_challenges"] is True
    assert prefs["direct_message_notifications"] is True
    assert prefs["tournament_updates"] is True
    assert prefs["league_updates"] is True
    assert prefs["achievement_notifications"] is True
    assert prefs["rating_milestones"] is True
    assert prefs["personal_best_notifications"] is True
    assert prefs["browser_sound"] is True
    assert prefs["visual_notifications"] is True


def test_update_preferences(
    identity_service: PlayerIdentityService, notification_service: NotificationService
):
    p = identity_service.create("Alice")
    updated = notification_service.update_preferences(
        p.player_id,
        {
            "friend_requests": False,
            "browser_sound": False,
            "tournament_updates": False,
        },
    )

    assert updated["friend_requests"] is False
    assert updated["browser_sound"] is False
    assert updated["tournament_updates"] is False
    assert updated["room_invitations"] is True  # untouched

    # Verify persistent retrieval
    fetched = notification_service.get_preferences(p.player_id)
    assert fetched["friend_requests"] is False
    assert fetched["browser_sound"] is False


def test_delivery_suppression_when_category_disabled(
    identity_service: PlayerIdentityService,
    friend_service: FriendService,
    invitation_service: InvitationService,
    messaging_service: MessagingService,
    notification_service: NotificationService,
):
    friend_service.notification_service = notification_service

    p1 = identity_service.create("Alice")
    p2 = identity_service.create("Bob")

    # Bob disables friend requests and direct messages
    notification_service.update_preferences(
        p2.player_id,
        {
            "friend_requests": False,
            "direct_message_notifications": False,
        },
    )

    # Alice sends friend request to Bob -> Bob should NOT receive notification
    req = friend_service.send_request(p1.player_id, p2.player_id)
    res = notification_service.list_notifications(p2.player_id)
    assert res["total"] == 0

    # Accept request to become friends
    friend_service.accept_request(req["request_id"], p2.player_id)

    # Alice sends DM to Bob -> Bob should NOT receive DM notification
    conv = messaging_service.get_or_create_conversation(p1.player_id, p2.player_id)
    messaging_service.send_message(conv["id"], p1.player_id, "Silent message")
    res = notification_service.list_notifications(p2.player_id)
    assert res["total"] == 0

    # But room invitations are still enabled for Bob -> Bob receives invitation notification
    class FakeEngine:
        async def get_room_metadata(self, room_code: str):
            return {
                "is_waiting": True,
                "is_full": False,
                "player_count": 1,
                "max_players": 4,
                "game_mode": "classic",
                "mode": "classic",
                "member_profile_ids": [p1.player_id],
                "host_profile_id": p1.player_id,
            }

    asyncio.run(invitation_service.create_invitation(
        inviter_id=p1.player_id,
        invitee_id=p2.player_id,
        room_code="ARENA9",
        engine=FakeEngine(),
    ))
    res = notification_service.list_notifications(p2.player_id)
    assert res["total"] == 1
    assert res["notifications"][0]["notification_type"] in ("room_invitation_received", "room_invitation")


def test_preferences_api_endpoints(test_db: Database):
    app = create_app(database=test_db)
    with TestClient(app) as client:
        res = client.post("/api/player-profiles", json={"display_name": "PrefTester"}).json()
        player_id, cred = res["player"]["id"], res["credential"]
        h = {"Authorization": f"Bearer {cred}"}

        # GET preferences
        get_res = client.get("/api/notifications/preferences", headers=h)
        assert get_res.status_code == 200
        data = get_res.json()
        assert data["friend_requests"] is True

        # PATCH preferences
        patch_res = client.patch(
            "/api/notifications/preferences",
            json={"friend_requests": False, "browser_sound": False},
            headers=h,
        )
        assert patch_res.status_code == 200
        patched_data = patch_res.json()
        assert patched_data["friend_requests"] is False
        assert patched_data["browser_sound"] is False

        # GET again to verify persistence
        get_res2 = client.get("/api/notifications/preferences", headers=h)
        assert get_res2.status_code == 200
        assert get_res2.json()["friend_requests"] is False
        assert get_res2.json()["browser_sound"] is False

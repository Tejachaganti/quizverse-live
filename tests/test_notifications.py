"""Comprehensive unit, integration, and security tests for Stage 9.5 Notification Center."""

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
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from quizverse.persistence.db import Database
from quizverse.persistence.invitations import InvitationService, RematchService
from quizverse.persistence.leagues import LeagueService
from quizverse.persistence.messaging import MessagingService
from quizverse.persistence.models import (
    NotificationPreference,
    PlayerNotification,
    PlayerProfile,
    utc_now,
)
from quizverse.persistence.notifications import NotificationService, SocialIntelligenceService
from quizverse.persistence.player_identity import PlayerIdentityService
from quizverse.persistence.social import BlockService, FriendService
from quizverse.persistence.tournaments import TournamentService
from quizverse.presence import LocalMemoryPresenceBroker, PresenceService
from quizverse.realtime import create_app


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'notifications-test.sqlite3').as_posix()}"
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
def messaging_service(
    test_db: Database, friend_service: FriendService, block_service: BlockService, notification_service: NotificationService
) -> MessagingService:
    svc = MessagingService(test_db, friend_service, block_service)
    svc.notification_service = notification_service
    return svc


@pytest.fixture
def invitation_service(
    test_db: Database, friend_service: FriendService, block_service: BlockService, notification_service: NotificationService
) -> InvitationService:
    svc = InvitationService(test_db, friend_service, block_service)
    svc.notification_service = notification_service
    return svc


@pytest.fixture
def rematch_service(
    test_db: Database, friend_service: FriendService, block_service: BlockService, notification_service: NotificationService
) -> RematchService:
    svc = RematchService(test_db, friend_service, block_service)
    svc.notification_service = notification_service
    return svc


def test_notification_creation_and_validation(
    test_db: Database, identity_service: PlayerIdentityService, notification_service: NotificationService
):
    p1 = identity_service.create("Alice")
    p2 = identity_service.create("Bob")

    # Valid creation
    notif = notification_service.create_notification(
        recipient_id=p1.player_id,
        actor_id=p2.player_id,
        notification_type="friend_request",
        title="Friend Request",
        body="Bob sent you a friend request.",
        target_type="friend",
        target_id=p2.player_id,
    )
    assert notif is not None
    assert notif["recipient_id"] == p1.player_id
    assert notif["actor_id"] == p2.player_id
    assert notif["is_read"] is False
    assert notif["title"] == "Friend Request"

    # Validation: empty recipient or empty title
    with pytest.raises(ValueError):
        notification_service.create_notification(
            recipient_id="",
            actor_id=p2.player_id,
            notification_type="friend_request",
            title="Friend Request",
            body="Bob sent you a friend request.",
        )

    with pytest.raises(ValueError):
        notification_service.create_notification(
            recipient_id=p1.player_id,
            actor_id=p2.player_id,
            notification_type="friend_request",
            title="",
            body="Bob sent you a friend request.",
        )


def test_notification_deduplication_engine(
    test_db: Database, identity_service: PlayerIdentityService, notification_service: NotificationService
):
    p1 = identity_service.create("Alice")
    p2 = identity_service.create("Bob")

    dedup_key = "dm:conv_123"
    notif1 = notification_service.create_notification(
        recipient_id=p1.player_id,
        actor_id=p2.player_id,
        notification_type="direct_message",
        title="New Message",
        body="Hello Alice!",
        deduplication_key=dedup_key,
    )
    assert notif1 is not None
    assert notif1["body"] == "Hello Alice!"

    # Second notification with the same deduplication key should update the existing row
    notif2 = notification_service.create_notification(
        recipient_id=p1.player_id,
        actor_id=p2.player_id,
        notification_type="direct_message",
        title="New Message",
        body="Are you there Alice?",
        deduplication_key=dedup_key,
    )
    assert notif2 is not None
    assert notif2["id"] == notif1["id"]
    assert notif2["body"] == "Are you there Alice?"

    # Count notifications for p1: must be exactly 1
    res = notification_service.list_notifications(p1.player_id)
    assert res["total"] == 1
    assert res["notifications"][0]["id"] == notif1["id"]
    assert res["notifications"][0]["body"] == "Are you there Alice?"

    # Verify database level unique constraint directly
    with pytest.raises(IntegrityError):
        with test_db.sessions.begin() as session:
            duplicate = PlayerNotification(
                id=secrets.token_hex(16),
                recipient_id=p1.player_id,
                actor_id=p2.player_id,
                notification_type="direct_message",
                title="Duplicate Attempt",
                body="Should fail db constraint",
                deduplication_key=dedup_key,
                created_at=utc_now(),
            )
            session.add(duplicate)


def test_read_unread_state_transitions(
    test_db: Database, identity_service: PlayerIdentityService, notification_service: NotificationService
):
    p1 = identity_service.create("Alice")

    n1 = notification_service.create_notification(
        recipient_id=p1.player_id, notification_type="system", title="Welcome 1", body="Welcome!"
    )
    n2 = notification_service.create_notification(
        recipient_id=p1.player_id, notification_type="system", title="Welcome 2", body="Welcome!"
    )
    n3 = notification_service.create_notification(
        recipient_id=p1.player_id, notification_type="system", title="Welcome 3", body="Welcome!"
    )

    assert notification_service.get_unread_count(p1.player_id) == 3

    # Mark single as read
    res1 = notification_service.mark_read(p1.player_id, n1["id"])
    assert res1["is_read"] is True
    assert notification_service.get_unread_count(p1.player_id) == 2

    # Mark single as unread
    res1_un = notification_service.mark_unread(p1.player_id, n1["id"])
    assert res1_un["is_read"] is False
    assert notification_service.get_unread_count(p1.player_id) == 3

    # Mark all read
    count = notification_service.mark_all_read(p1.player_id)
    assert count == 3
    assert notification_service.get_unread_count(p1.player_id) == 0


def test_notification_deletion_and_clear_read(
    test_db: Database, identity_service: PlayerIdentityService, notification_service: NotificationService
):
    p1 = identity_service.create("Alice")

    n1 = notification_service.create_notification(
        recipient_id=p1.player_id, notification_type="system", title="N1", body="Msg 1"
    )
    n2 = notification_service.create_notification(
        recipient_id=p1.player_id, notification_type="system", title="N2", body="Msg 2"
    )
    n3 = notification_service.create_notification(
        recipient_id=p1.player_id, notification_type="system", title="N3", body="Msg 3"
    )

    # Mark n1 and n2 as read
    notification_service.mark_read(p1.player_id, n1["id"])
    notification_service.mark_read(p1.player_id, n2["id"])

    # Delete single notification n1
    deleted = notification_service.delete_notification(p1.player_id, n1["id"])
    assert deleted is True

    res = notification_service.list_notifications(p1.player_id)
    assert res["total"] == 2
    assert all(item["id"] != n1["id"] for item in res["notifications"])

    # Clear read notifications: removes n2, keeps unread n3
    cleared = notification_service.delete_read_notifications(p1.player_id)
    assert cleared == 1

    res = notification_service.list_notifications(p1.player_id)
    assert res["total"] == 1
    assert res["notifications"][0]["id"] == n3["id"]


def test_retention_pruning(
    test_db: Database, identity_service: PlayerIdentityService, notification_service: NotificationService
):
    p1 = identity_service.create("Alice")
    now = utc_now()

    with test_db.sessions.begin() as session:
        # 1. Old read (> 30 days) -> should be pruned
        old_read = PlayerNotification(
            id="old_read_1",
            recipient_id=p1.player_id,
            notification_type="system",
            title="Old Read",
            body="Prune me",
            is_read=True,
            read_at=now - timedelta(days=35),
            created_at=now - timedelta(days=35),
        )
        # 2. Old unread (> 90 days) -> should be pruned
        old_unread = PlayerNotification(
            id="old_unread_1",
            recipient_id=p1.player_id,
            notification_type="system",
            title="Old Unread",
            body="Prune me too",
            is_read=False,
            created_at=now - timedelta(days=95),
        )
        # 3. Recent read (< 30 days) -> should be kept
        recent_read = PlayerNotification(
            id="recent_read_1",
            recipient_id=p1.player_id,
            notification_type="system",
            title="Recent Read",
            body="Keep me",
            is_read=True,
            read_at=now - timedelta(days=5),
            created_at=now - timedelta(days=5),
        )
        # 4. Old achievement (> 100 days) -> should ALWAYS be kept
        old_achievement = PlayerNotification(
            id="old_achieve_1",
            recipient_id=p1.player_id,
            notification_type="achievement_unlocked",
            title="Grand Champion",
            body="Trophy forever",
            is_read=True,
            read_at=now - timedelta(days=120),
            created_at=now - timedelta(days=120),
        )
        session.add_all([old_read, old_unread, recent_read, old_achievement])

    pruned = notification_service.prune_retained_notifications(batch_size=50)
    assert pruned == 2

    res = notification_service.list_notifications(p1.player_id)
    remaining_ids = {n["id"] for n in res["notifications"]}
    assert "old_read_1" not in remaining_ids
    assert "old_unread_1" not in remaining_ids
    assert "recent_read_1" in remaining_ids
    assert "old_achieve_1" in remaining_ids


def test_cross_service_event_triggers(
    test_db: Database,
    identity_service: PlayerIdentityService,
    friend_service: FriendService,
    invitation_service: InvitationService,
    messaging_service: MessagingService,
    notification_service: NotificationService,
):
    friend_service.notification_service = notification_service
    invitation_service.notification_service = notification_service
    messaging_service.notification_service = notification_service

    p1 = identity_service.create("Alice")
    p2 = identity_service.create("Bob")

    # 1. Friend Request Sent
    req = friend_service.send_request(p1.player_id, p2.player_id)
    res = notification_service.list_notifications(p2.player_id)
    assert res["total"] >= 1
    assert res["notifications"][0]["notification_type"] in ("friend_request_received", "friend_request")
    assert res["notifications"][0]["actor_id"] == p1.player_id

    # 2. Friend Request Accepted
    friend_service.accept_request(req["request_id"], p2.player_id)
    res = notification_service.list_notifications(p1.player_id)
    assert any(n["notification_type"] in ("friend_request_accepted", "friend_accepted") for n in res["notifications"])

    # 3. Room Invitation Sent
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
        room_code="ARENA1",
        engine=FakeEngine(),
    ))
    res = notification_service.list_notifications(p2.player_id)
    assert any(n["notification_type"] in ("room_invitation_received", "room_invitation") for n in res["notifications"])

    # 4. Direct Message Sent & Read
    conv = messaging_service.get_or_create_conversation(p1.player_id, p2.player_id)
    messaging_service.send_message(conv["id"], p1.player_id, "Hey Bob!")
    res = notification_service.list_notifications(p2.player_id)
    dm_notifs = [n for n in res["notifications"] if n["notification_type"] in ("direct_message", "direct_message_received")]
    assert len(dm_notifs) == 1
    assert dm_notifs[0]["is_read"] is False

    # Marking conversation as read marks the notification as read
    messaging_service.mark_as_read(conv["id"], p2.player_id)
    res = notification_service.list_notifications(p2.player_id)
    dm_notifs = [n for n in res["notifications"] if n["notification_type"] in ("direct_message", "direct_message_received")]
    assert dm_notifs[0]["is_read"] is True


def test_notification_rest_api_endpoints(test_db: Database):
    app = create_app(database=test_db)
    with TestClient(app) as client:
        p1_res = client.post("/api/player-profiles", json={"display_name": "Alice"}).json()
        p1_id, cred1 = p1_res["player"]["id"], p1_res["credential"]

        p2_res = client.post("/api/player-profiles", json={"display_name": "Bob"}).json()
        p2_id, cred2 = p2_res["player"]["id"], p2_res["credential"]

        h1 = {"Authorization": f"Bearer {cred1}"}
        h2 = {"Authorization": f"Bearer {cred2}"}

        # Unauthorized access returns 401
        resp = client.get("/api/notifications")
        assert resp.status_code == 401

        # Empty initially
        resp = client.get("/api/notifications", headers=h1)
        assert resp.status_code == 200
        assert resp.json()["notifications"] == []
        assert resp.json()["total"] == 0

        # Create notifications via notification_service
        notif_svc: NotificationService = app.state.notification_service
        n1 = notif_svc.create_notification(p1_id, notification_type="friend_request", title="Hey", body="Body 1")
        n2 = notif_svc.create_notification(p1_id, notification_type="tournament_champion", title="Tourn", body="Body 2")

        # Unread count
        resp = client.get("/api/notifications/unread-count", headers=h1)
        assert resp.status_code == 200
        assert resp.json()["unread_count"] == 2

        # Filter by category
        resp = client.get("/api/notifications?filter=tournaments", headers=h1)
        assert resp.status_code == 200
        assert len(resp.json()["notifications"]) == 1
        assert resp.json()["notifications"][0]["id"] == n2["id"]

        # Mark n1 read
        resp = client.post(f"/api/notifications/{n1['id']}/read", headers=h1)
        assert resp.status_code == 200
        assert resp.json()["is_read"] is True

        # Mark n1 unread
        resp = client.post(f"/api/notifications/{n1['id']}/unread", headers=h1)
        assert resp.status_code == 200
        assert resp.json()["is_read"] is False

        # Mark all read
        resp = client.post("/api/notifications/mark-all-read", headers=h1)
        assert resp.status_code == 200
        assert resp.json()["marked_read_count"] == 2

        # Player 2 cannot delete Player 1's notification
        resp = client.delete(f"/api/notifications/{n1['id']}", headers=h2)
        assert resp.status_code in (403, 404)

        # Player 1 deletes n1
        resp = client.delete(f"/api/notifications/{n1['id']}", headers=h1)
        assert resp.status_code == 200

        # Clear read
        resp = client.delete("/api/notifications/clear-read", headers=h1)
        assert resp.status_code == 200
        assert resp.json()["cleared_count"] == 1

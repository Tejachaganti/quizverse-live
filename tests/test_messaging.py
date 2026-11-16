"""Comprehensive unit, integration, and security tests for Stage 9.4 1-on-1 private messaging."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from quizverse.persistence.db import Database
from quizverse.persistence.messaging import (
    ALLOWED_REACTIONS,
    ALLOWED_REPORT_REASONS,
    MessagingError,
    MessagingService,
)
from quizverse.persistence.models import (
    DirectConversation,
    DirectMessage,
    DirectMessageReaction,
    Friendship,
    MessageReport,
    PlayerBlock,
    PlayerProfile,
    utc_now,
)
from quizverse.persistence.player_identity import PlayerIdentityService
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
    url = f"sqlite:///{(tmp_path / 'messaging-test.sqlite3').as_posix()}"
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
def identity_service(test_db: Database) -> PlayerIdentityService:
    return PlayerIdentityService(test_db)


@pytest.fixture
def block_service(test_db: Database) -> BlockService:
    return BlockService(test_db)


@pytest.fixture
def friend_service(test_db: Database, block_service: BlockService) -> FriendService:
    return FriendService(test_db, block_service)


@pytest.fixture
def messaging_service(test_db: Database, friend_service: FriendService, block_service: BlockService) -> MessagingService:
    return MessagingService(test_db, friend_service, block_service)


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


def test_conversation_creation_and_canonical_uniqueness(
    test_db: Database,
    identity_service: PlayerIdentityService,
    messaging_service: MessagingService,
):
    """Ensure conversations enforce canonical pair ordering and uniqueness."""
    p1 = identity_service.create("Alice")
    p2 = identity_service.create("Bob")
    _create_friendship(test_db, p1.player_id, p2.player_id)

    # Alice initiates
    c1 = messaging_service.get_or_create_conversation(p1.player_id, p2.player_id)
    assert c1["id"] is not None
    assert c1["friend"]["player_id"] == p2.player_id

    # Bob initiates -> returns same conversation
    c2 = messaging_service.get_or_create_conversation(p2.player_id, p1.player_id)
    assert c2["id"] == c1["id"]
    assert c2["friend"]["player_id"] == p1.player_id

    # Exactly one record exists in DB
    with test_db.session() as s:
        convs = s.scalars(select(DirectConversation)).all()
        assert len(convs) == 1
        assert convs[0].player_one_id < convs[0].player_two_id


def test_self_conversation_rejected(
    identity_service: PlayerIdentityService,
    messaging_service: MessagingService,
):
    """Verify player cannot message themselves."""
    p = identity_service.create("Selfie")
    with pytest.raises(MessagingError) as exc_info:
        messaging_service.get_or_create_conversation(p.player_id, p.player_id)
    assert exc_info.value.code == "cannot_message_self"


def test_friend_only_messaging(
    test_db: Database,
    identity_service: PlayerIdentityService,
    messaging_service: MessagingService,
):
    """Verify non-friends are rejected from initiating or sending messages."""
    p1 = identity_service.create("Stranger1")
    p2 = identity_service.create("Stranger2")

    # Not friends -> cannot create conversation
    with pytest.raises(MessagingError) as exc_info:
        messaging_service.get_or_create_conversation(p1.player_id, p2.player_id)
    assert exc_info.value.code == "not_friends"


def test_blocking_enforcement(
    test_db: Database,
    identity_service: PlayerIdentityService,
    block_service: BlockService,
    messaging_service: MessagingService,
):
    """Verify blocking prevents conversation initiation and message sending."""
    p1 = identity_service.create("PlayerA")
    p2 = identity_service.create("PlayerB")
    _create_friendship(test_db, p1.player_id, p2.player_id)

    conv = messaging_service.get_or_create_conversation(p1.player_id, p2.player_id)

    # PlayerA blocks PlayerB
    block_service.block_player(p1.player_id, p2.player_id)

    # Attempting to message now fails
    with pytest.raises(MessagingError) as exc_info:
        messaging_service.send_message(conv["id"], p2.player_id, "Hello?")
    assert exc_info.value.code == "blocked"

    with pytest.raises(MessagingError) as exc_info2:
        messaging_service.send_message(conv["id"], p1.player_id, "Hello?")
    assert exc_info2.value.code == "blocked"


def test_send_and_list_messages_pagination(
    test_db: Database,
    identity_service: PlayerIdentityService,
    messaging_service: MessagingService,
):
    """Test message sending, pagination, and unread counts."""
    p1 = identity_service.create("Sender")
    p2 = identity_service.create("Receiver")
    _create_friendship(test_db, p1.player_id, p2.player_id)

    conv = messaging_service.get_or_create_conversation(p1.player_id, p2.player_id)

    # Send 5 messages
    for i in range(5):
        msg = messaging_service.send_message(conv["id"], p1.player_id, f"Message {i}")
        assert msg["content"] == f"Message {i}"
        assert msg["recipient_id"] == p2.player_id

    # Verify conversation unread count for receiver is 5
    conv_details = messaging_service.get_conversation(conv["id"], p2.player_id)
    assert conv_details["unread_count"] == 5

    # List messages paginated
    history = messaging_service.list_messages(conv["id"], p2.player_id, limit=3)
    assert len(history["messages"]) == 3
    assert history["has_more"] is True

    # Use before_id to get older messages
    oldest_id = history["messages"][0]["id"]
    older = messaging_service.list_messages(conv["id"], p2.player_id, limit=3, before_id=oldest_id)
    assert len(older["messages"]) == 2
    assert older["has_more"] is False


def test_unauthorized_conversation_access(
    test_db: Database,
    identity_service: PlayerIdentityService,
    messaging_service: MessagingService,
):
    """Ensure third-party players cannot view or send messages in other players' conversations."""
    p1 = identity_service.create("P1")
    p2 = identity_service.create("P2")
    p3 = identity_service.create("Snooper")
    _create_friendship(test_db, p1.player_id, p2.player_id)

    conv = messaging_service.get_or_create_conversation(p1.player_id, p2.player_id)

    with pytest.raises(MessagingError) as exc_info:
        messaging_service.list_messages(conv["id"], p3.player_id)
    assert exc_info.value.code == "access_denied"

    with pytest.raises(MessagingError) as exc_info2:
        messaging_service.send_message(conv["id"], p3.player_id, "Intruder")
    assert exc_info2.value.code == "access_denied"


def test_edit_message_window_and_ownership(
    test_db: Database,
    identity_service: PlayerIdentityService,
    messaging_service: MessagingService,
):
    """Verify edit ownership and 5-minute time window enforcement."""
    p1 = identity_service.create("Author")
    p2 = identity_service.create("Peer")
    _create_friendship(test_db, p1.player_id, p2.player_id)

    conv = messaging_service.get_or_create_conversation(p1.player_id, p2.player_id)
    msg = messaging_service.send_message(conv["id"], p1.player_id, "Original typo")

    # Peer cannot edit Author's message
    with pytest.raises(MessagingError) as exc_peer:
        messaging_service.edit_message(msg["id"], p2.player_id, "Peer edited")
    assert exc_peer.value.code == "unauthorized"

    # Author edits within window -> succeeds
    edited = messaging_service.edit_message(msg["id"], p1.player_id, "Corrected text")
    assert edited["content"] == "Corrected text"

    # Simulated past message (> 5 minutes ago)
    past_time = utc_now() - timedelta(minutes=6)
    with test_db.sessions.begin() as s:
        db_msg = s.get(DirectMessage, msg["id"])
        db_msg.created_at = past_time

    with pytest.raises(MessagingError) as exc_window:
        messaging_service.edit_message(msg["id"], p1.player_id, "Too late")
    assert exc_window.value.code == "edit_window_expired"


def test_delete_message_soft_delete(
    test_db: Database,
    identity_service: PlayerIdentityService,
    messaging_service: MessagingService,
):
    """Verify soft-delete clears content to placeholder and protects privacy."""
    p1 = identity_service.create("Deleter")
    p2 = identity_service.create("Reader")
    _create_friendship(test_db, p1.player_id, p2.player_id)

    conv = messaging_service.get_or_create_conversation(p1.player_id, p2.player_id)
    msg = messaging_service.send_message(conv["id"], p1.player_id, "Secret content")

    # Other player cannot delete
    with pytest.raises(MessagingError) as exc_auth:
        messaging_service.delete_message(msg["id"], p2.player_id)
    assert exc_auth.value.code == "unauthorized"

    # Author deletes
    res = messaging_service.delete_message(msg["id"], p1.player_id)
    assert res["deleted"] is True

    # Read messages as Reader -> content is placeholder
    msgs = messaging_service.list_messages(conv["id"], p2.player_id)
    deleted_msg = msgs["messages"][0]
    assert deleted_msg["is_deleted"] is True
    assert deleted_msg["content"] == "[Message deleted]"


def test_read_receipts(
    test_db: Database,
    identity_service: PlayerIdentityService,
    messaging_service: MessagingService,
):
    """Verify marking messages as read resets unread count and updates status."""
    p1 = identity_service.create("SenderR")
    p2 = identity_service.create("ReceiverR")
    _create_friendship(test_db, p1.player_id, p2.player_id)

    conv = messaging_service.get_or_create_conversation(p1.player_id, p2.player_id)
    msg = messaging_service.send_message(conv["id"], p1.player_id, "Unread message")

    # Before read
    c_before = messaging_service.get_conversation(conv["id"], p2.player_id)
    assert c_before["unread_count"] == 1

    # Receiver marks read
    read_res = messaging_service.mark_as_read(conv["id"], p2.player_id)
    assert msg["id"] in read_res["marked_message_ids"]

    # After read
    c_after = messaging_service.get_conversation(conv["id"], p2.player_id)
    assert c_after["unread_count"] == 0

    # Message shows read status
    history = messaging_service.list_messages(conv["id"], p1.player_id)
    assert history["messages"][0]["status"] == "read"


def test_reactions_allowlist_and_toggle(
    test_db: Database,
    identity_service: PlayerIdentityService,
    messaging_service: MessagingService,
):
    """Verify emoji reactions allowlist, duplicate handling, and removal."""
    p1 = identity_service.create("R1")
    p2 = identity_service.create("R2")
    _create_friendship(test_db, p1.player_id, p2.player_id)

    conv = messaging_service.get_or_create_conversation(p1.player_id, p2.player_id)
    msg = messaging_service.send_message(conv["id"], p1.player_id, "React to this")

    # Invalid reaction rejected
    with pytest.raises(MessagingError) as exc_rx:
        messaging_service.add_reaction(msg["id"], p2.player_id, "🚀")
    assert exc_rx.value.code == "invalid_reaction"

    # Valid reaction succeeds
    res = messaging_service.add_reaction(msg["id"], p2.player_id, "🔥")
    assert "🔥" in res["reactions"]
    assert p2.player_id in res["reactions"]["🔥"]

    # Remove reaction
    removed = messaging_service.remove_reaction(msg["id"], p2.player_id, "🔥")
    assert "🔥" not in removed["reactions"]


def test_message_reporting(
    test_db: Database,
    identity_service: PlayerIdentityService,
    messaging_service: MessagingService,
):
    """Verify safety reporting and duplicate prevention."""
    p1 = identity_service.create("BadActor")
    p2 = identity_service.create("Reporter")
    _create_friendship(test_db, p1.player_id, p2.player_id)

    conv = messaging_service.get_or_create_conversation(p1.player_id, p2.player_id)
    msg = messaging_service.send_message(conv["id"], p1.player_id, "Nasty message")

    # Author cannot report self
    with pytest.raises(MessagingError) as exc_self:
        messaging_service.report_message(msg["id"], p1.player_id, "spam")
    assert exc_self.value.code == "cannot_report_self"

    # Reporter reports message
    rep = messaging_service.report_message(msg["id"], p2.player_id, "harassment", "Offensive words")
    assert rep["reason"] == "harassment"
    assert rep["status"] == "pending"

    # Duplicate report rejected
    with pytest.raises(MessagingError) as exc_dup:
        messaging_service.report_message(msg["id"], p2.player_id, "harassment")
    assert exc_dup.value.code == "already_reported"


def test_retention_cleanup(
    test_db: Database,
    identity_service: PlayerIdentityService,
    messaging_service: MessagingService,
):
    """Verify messages older than 30 days are purged by retention policy."""
    p1 = identity_service.create("Ret1")
    p2 = identity_service.create("Ret2")
    _create_friendship(test_db, p1.player_id, p2.player_id)

    conv = messaging_service.get_or_create_conversation(p1.player_id, p2.player_id)

    # Fresh message
    m_fresh = messaging_service.send_message(conv["id"], p1.player_id, "Fresh message")

    # Old message manually backdated to 35 days ago
    old_time = utc_now() - timedelta(days=35)
    with test_db.sessions.begin() as s:
        old_m = DirectMessage(
            id=secrets.token_hex(16),
            conversation_id=conv["id"],
            sender_id=p1.player_id,
            content="Old message",
            created_at=old_time,
        )
        s.add(old_m)

    # Run cleanup with 30 days
    purged = messaging_service.cleanup_expired_messages(retention_days=30)
    assert purged == 1

    # Only fresh message remains
    history = messaging_service.list_messages(conv["id"], p2.player_id)
    assert len(history["messages"]) == 1
    assert history["messages"][0]["id"] == m_fresh["id"]


def test_http_api_endpoints(test_db: Database):
    """Test full HTTP API flows via TestClient."""
    app = create_app(database=test_db)
    with TestClient(app) as client:
        # Register 2 players
        r1 = client.post("/api/player-profiles", json={"display_name": "ApiPlayer1"})
        assert r1.status_code == 201, r1.text
        t1 = r1.json()["credential"]
        pid1 = r1.json()["player"]["id"]

        r2 = client.post("/api/player-profiles", json={"display_name": "ApiPlayer2"})
        assert r2.status_code == 201, r2.text
        t2 = r2.json()["credential"]
        pid2 = r2.json()["player"]["id"]

        _create_friendship(test_db, pid1, pid2)

        # 1. Create conversation
        res_c = client.post(
            "/api/messages/conversations",
            headers={"Authorization": f"Bearer {t1}"},
            json={"friend_id": pid2},
        )
        assert res_c.status_code == 200, res_c.text
        conv_id = res_c.json()["id"]

        # 2. List conversations
        res_list = client.get("/api/messages/conversations", headers={"Authorization": f"Bearer {t1}"})
        assert res_list.status_code == 200
        assert len(res_list.json()["conversations"]) == 1

        # 3. Send message
        res_msg = client.post(
            f"/api/messages/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {t1}"},
            json={"content": "Hello from API!"},
        )
        assert res_msg.status_code == 201
        msg_id = res_msg.json()["id"]

        # 4. Fetch messages
        res_msgs = client.get(
            f"/api/messages/conversations/{conv_id}/messages",
            headers={"Authorization": f"Bearer {t2}"},
        )
        assert res_msgs.status_code == 200
        assert len(res_msgs.json()["messages"]) == 1

        # 5. Add reaction
        res_rx = client.post(
            f"/api/messages/{msg_id}/reactions",
            headers={"Authorization": f"Bearer {t2}"},
            json={"reaction": "👍"},
        )
        assert res_rx.status_code == 200
        assert "👍" in res_rx.json()["reactions"]

        # 6. Mark read
        res_read = client.post(
            f"/api/messages/{conv_id}/read",
            headers={"Authorization": f"Bearer {t2}"},
        )
        assert res_read.status_code == 200

        # 7. Edit message
        res_edit = client.patch(
            f"/api/messages/{msg_id}",
            headers={"Authorization": f"Bearer {t1}"},
            json={"content": "Edited API message"},
        )
        assert res_edit.status_code == 200

        # 8. Report message
        res_rep = client.post(
            f"/api/messages/{msg_id}/report",
            headers={"Authorization": f"Bearer {t2}"},
            json={"reason": "spam", "details": "Testing report"},
        )
        assert res_rep.status_code == 201

        # 9. Delete message
        res_del = client.delete(
            f"/api/messages/{msg_id}",
            headers={"Authorization": f"Bearer {t1}"},
        )
        assert res_del.status_code == 200

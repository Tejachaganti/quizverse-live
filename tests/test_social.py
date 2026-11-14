"""Comprehensive unit and integration tests for Stage 9.1 Social Foundation, Player Discovery, and Friend Management."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from quizverse.realtime import create_app
from quizverse.persistence.competition import AdvancedStatsService, HonorsService
from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    FriendRequest,
    Friendship,
    PlayerBlock,
    PlayerProfile,
    PlayerRating,
)
from quizverse.persistence.player_identity import PlayerIdentityService
from quizverse.persistence.ratings import RatingService
from quizverse.persistence.social import (
    BlockService,
    FriendService,
    PlayerDiscoveryService,
    SocialError,
)


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'social-test.sqlite3').as_posix()}"
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
def stats_service(test_db):
    return AdvancedStatsService(test_db)


@pytest.fixture
def honors_service(test_db):
    return HonorsService(test_db)


@pytest.fixture
def block_service(test_db):
    return BlockService(test_db)


@pytest.fixture
def friend_service(test_db, block_service, ratings):
    return FriendService(test_db, block_service, ratings)


@pytest.fixture
def discovery_service(test_db, friend_service, block_service, stats_service, honors_service):
    return PlayerDiscoveryService(test_db, friend_service, block_service, stats_service, honors_service)


# ---------------------------------------------------------------------------
# 1. Player Discovery & Search Tests
# ---------------------------------------------------------------------------

def test_player_discovery_search_by_name(identities, discovery_service):
    p1 = identities.create("SearcherA")
    p2 = identities.create("AliceWonder")
    p3 = identities.create("AliciaKeys")
    p4 = identities.create("BobBuilder")

    # Substring search case-insensitive
    results = discovery_service.search_players(p1.player_id, "ali")
    names = [r["display_name"] for r in results]
    assert "AliceWonder" in names
    assert "AliciaKeys" in names
    assert "BobBuilder" not in names
    assert "SearcherA" not in names  # Self exclusion


def test_player_discovery_search_by_id(identities, discovery_service):
    p1 = identities.create("SearcherB")
    p2 = identities.create("TargetUser")

    # Search by 32-hex ID
    results = discovery_service.search_players(p1.player_id, p2.player_id)
    assert len(results) == 1
    assert results[0]["player_id"] == p2.player_id
    assert results[0]["display_name"] == "TargetUser"


def test_player_discovery_query_validation(identities, discovery_service):
    p = identities.create("Validator")

    # Query too short (< 2 chars)
    with pytest.raises(SocialError) as exc:
        discovery_service.search_players(p.player_id, "a")
    assert exc.value.code == "query_too_short"
    assert exc.value.status_code == 422

    with pytest.raises(SocialError) as exc:
        discovery_service.search_players(p.player_id, "   ")
    assert exc.value.code == "query_too_short"


def test_player_discovery_excludes_blocked_users(identities, block_service, discovery_service):
    p1 = identities.create("PlayerAlpha")
    p2 = identities.create("PlayerBeta")
    p3 = identities.create("PlayerGamma")

    # Alpha blocks Beta
    block_service.block_player(p1.player_id, p2.player_id)

    # Alpha searches -> Beta must be excluded
    results_alpha = discovery_service.search_players(p1.player_id, "Player")
    ids_alpha = [r["player_id"] for r in results_alpha]
    assert p2.player_id not in ids_alpha
    assert p3.player_id in ids_alpha

    # Beta searches -> Alpha must also be excluded (bidirectional privacy)
    results_beta = discovery_service.search_players(p2.player_id, "Player")
    ids_beta = [r["player_id"] for r in results_beta]
    assert p1.player_id not in ids_beta
    assert p3.player_id in ids_beta


def test_player_discovery_public_profile(identities, discovery_service, test_db):
    p1 = identities.create("Viewer")
    p2 = identities.create("StarPlayer")

    # Add a rating record
    with test_db.sessions.begin() as s:
        s.add(
            PlayerRating(
                id=secrets.token_hex(16),
                player_id=p2.player_id,
                rating_scope="global",
                rating=1250,
                rated_match_count=15,
                wins=10,
                losses=5,
                draws=0,
            )
        )

    profile = discovery_service.get_public_profile(p1.player_id, p2.player_id)
    assert profile["player_id"] == p2.player_id
    assert profile["display_name"] == "StarPlayer"
    assert profile["rating"] == 1250
    assert profile["relationship_state"] == "none"
    assert "token" not in profile


# ---------------------------------------------------------------------------
# 2. Friend Request Lifecycle Tests
# ---------------------------------------------------------------------------

def test_friend_request_send_and_accept_lifecycle(identities, friend_service, test_db):
    p1 = identities.create("SenderX")
    p2 = identities.create("ReceiverY")

    # Send friend request
    res = friend_service.send_request(p1.player_id, p2.player_id)
    assert res["status"] == "pending"
    assert res["friendship_created"] is False
    req_id = res["request_id"]

    # Check incoming requests for receiver
    incoming = friend_service.list_incoming_requests(p2.player_id)
    assert len(incoming) == 1
    assert incoming[0]["request_id"] == req_id
    assert incoming[0]["sender_id"] == p1.player_id

    # Check outgoing requests for sender
    outgoing = friend_service.list_outgoing_requests(p1.player_id)
    assert len(outgoing) == 1
    assert outgoing[0]["request_id"] == req_id
    assert outgoing[0]["recipient_id"] == p2.player_id

    # Accept request
    accept_res = friend_service.accept_request(req_id, p2.player_id)
    assert accept_res["status"] == "accepted"
    assert accept_res["friend_id"] == p1.player_id

    # Check friendship created in canonical order
    assert friend_service.are_friends(p1.player_id, p2.player_id) is True
    assert friend_service.are_friends(p2.player_id, p1.player_id) is True

    # Both players see each other in their friend list
    f1 = friend_service.list_friends(p1.player_id)
    f2 = friend_service.list_friends(p2.player_id)
    assert len(f1) == 1 and f1[0]["player_id"] == p2.player_id
    assert len(f2) == 1 and f2[0]["player_id"] == p1.player_id

    # Pending lists are now empty
    assert len(friend_service.list_incoming_requests(p2.player_id)) == 0
    assert len(friend_service.list_outgoing_requests(p1.player_id)) == 0


def test_friend_request_reject(identities, friend_service):
    p1 = identities.create("Requester")
    p2 = identities.create("Rejecter")

    res = friend_service.send_request(p1.player_id, p2.player_id)
    req_id = res["request_id"]

    reject_res = friend_service.reject_request(req_id, p2.player_id)
    assert reject_res["status"] == "rejected"

    assert friend_service.are_friends(p1.player_id, p2.player_id) is False
    assert len(friend_service.list_incoming_requests(p2.player_id)) == 0


def test_friend_request_cancel(identities, friend_service):
    p1 = identities.create("Changer")
    p2 = identities.create("FriendCandidate")

    res = friend_service.send_request(p1.player_id, p2.player_id)
    req_id = res["request_id"]

    cancel_res = friend_service.cancel_request(req_id, p1.player_id)
    assert cancel_res["status"] == "cancelled"

    assert len(friend_service.list_outgoing_requests(p1.player_id)) == 0


def test_friend_request_simultaneous_opposite_auto_accept(identities, friend_service):
    p1 = identities.create("MutualA")
    p2 = identities.create("MutualB")

    # MutualA sends to MutualB
    r1 = friend_service.send_request(p1.player_id, p2.player_id)
    assert r1["status"] == "pending"

    # MutualB sends to MutualA at the same time: auto-accept!
    r2 = friend_service.send_request(p2.player_id, p1.player_id)
    assert r2["status"] == "accepted"
    assert r2["friendship_created"] is True

    # They are friends immediately
    assert friend_service.are_friends(p1.player_id, p2.player_id) is True


def test_friend_request_validation_and_errors(identities, friend_service):
    p1 = identities.create("ErrorA")
    p2 = identities.create("ErrorB")

    # Cannot friend self
    with pytest.raises(SocialError) as exc:
        friend_service.send_request(p1.player_id, p1.player_id)
    assert exc.value.code == "cannot_friend_self"

    # Duplicate request rejected
    friend_service.send_request(p1.player_id, p2.player_id)
    with pytest.raises(SocialError) as exc:
        friend_service.send_request(p1.player_id, p2.player_id)
    assert exc.value.code == "duplicate_request"

    # Unauthorized accept (recipient only)
    req = friend_service.list_outgoing_requests(p1.player_id)[0]
    with pytest.raises(SocialError) as exc:
        friend_service.accept_request(req["request_id"], p1.player_id)  # sender tries to accept
    assert exc.value.code == "request_not_found"

    # Cannot friend already established friend
    friend_service.accept_request(req["request_id"], p2.player_id)
    with pytest.raises(SocialError) as exc:
        friend_service.send_request(p1.player_id, p2.player_id)
    assert exc.value.code == "already_friends"


# ---------------------------------------------------------------------------
# 3. Canonical Friendship & Removal Tests
# ---------------------------------------------------------------------------

def test_canonical_friendship_ordering_and_removal(identities, friend_service, test_db):
    p1 = identities.create("FriendAlpha")
    p2 = identities.create("FriendBeta")

    # Ensure p1 < p2 regardless of who initiated
    id_min = min(p1.player_id, p2.player_id)
    id_max = max(p1.player_id, p2.player_id)

    res = friend_service.send_request(p1.player_id, p2.player_id)
    friend_service.accept_request(res["request_id"], p2.player_id)

    with test_db.session() as s:
        f = s.scalar(select(Friendship).where(Friendship.player_one_id == id_min, Friendship.player_two_id == id_max))
        assert f is not None
        assert f.player_one_id < f.player_two_id

    # Remove friend
    removed = friend_service.remove_friend(p1.player_id, p2.player_id)
    assert removed is True
    assert friend_service.are_friends(p1.player_id, p2.player_id) is False

    with test_db.session() as s:
        f = s.scalar(select(Friendship).where(Friendship.player_one_id == id_min, Friendship.player_two_id == id_max))
        assert f is None


# ---------------------------------------------------------------------------
# 4. Player Blocking Tests
# ---------------------------------------------------------------------------

def test_player_block_lifecycle_and_severs_friendship(identities, friend_service, block_service):
    p1 = identities.create("BlockerA")
    p2 = identities.create("BlockedB")

    # Establish friendship first
    req = friend_service.send_request(p1.player_id, p2.player_id)
    friend_service.accept_request(req["request_id"], p2.player_id)
    assert friend_service.are_friends(p1.player_id, p2.player_id) is True

    # BlockerA blocks BlockedB
    block_info = block_service.block_player(p1.player_id, p2.player_id)
    assert block_info["blocked_id"] == p2.player_id

    # Friendship severed!
    assert friend_service.are_friends(p1.player_id, p2.player_id) is False
    assert len(friend_service.list_friends(p1.player_id)) == 0
    assert len(friend_service.list_friends(p2.player_id)) == 0

    # Blocked players list
    blocked_list = block_service.list_blocked_players(p1.player_id)
    assert len(blocked_list) == 1
    assert blocked_list[0]["player_id"] == p2.player_id

    # Requests cannot be sent in EITHER direction
    with pytest.raises(SocialError) as exc:
        friend_service.send_request(p1.player_id, p2.player_id)
    assert exc.value.code == "blocked_player"

    with pytest.raises(SocialError) as exc:
        friend_service.send_request(p2.player_id, p1.player_id)
    assert exc.value.code == "blocked_player"

    # Unblock
    unblocked = block_service.unblock_player(p1.player_id, p2.player_id)
    assert unblocked is True
    assert len(block_service.list_blocked_players(p1.player_id)) == 0

    # After unblock, friend request can be sent again
    new_req = friend_service.send_request(p1.player_id, p2.player_id)
    assert new_req["status"] == "pending"


def test_blocking_cancels_pending_requests(identities, friend_service, block_service):
    p1 = identities.create("PendingA")
    p2 = identities.create("PendingB")

    # Send request
    friend_service.send_request(p1.player_id, p2.player_id)
    assert len(friend_service.list_incoming_requests(p2.player_id)) == 1

    # B blocks A
    block_service.block_player(p2.player_id, p1.player_id)

    # Pending request cancelled and cleared from lists
    assert len(friend_service.list_incoming_requests(p2.player_id)) == 0
    assert len(friend_service.list_outgoing_requests(p1.player_id)) == 0


def test_cannot_block_self(identities, block_service):
    p = identities.create("SelfBlocker")
    with pytest.raises(SocialError) as exc:
        block_service.block_player(p.player_id, p.player_id)
    assert exc.value.code == "cannot_block_self"


# ---------------------------------------------------------------------------
# 5. REST API Flow & Security Tests
# ---------------------------------------------------------------------------

def test_social_api_flow(test_db):
    with TestClient(create_app(database=test_db)) as client:
        # 1. Create two profiles
        res_a = client.post("/api/player-profiles", json={"display_name": "ApiPlayerA"})
        res_b = client.post("/api/player-profiles", json={"display_name": "ApiPlayerB"})
        assert res_a.status_code == 201
        assert res_b.status_code == 201

        token_a = res_a.json()["credential"]
        id_a = res_a.json()["player"]["id"]
        token_b = res_b.json()["credential"]
        id_b = res_b.json()["player"]["id"]

        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # 2. Player A searches for Player B
        search_res = client.get("/api/players/search?query=ApiPlayer", headers=headers_a)
        assert search_res.status_code == 200
        results = search_res.json()["results"]
        assert any(r["player_id"] == id_b for r in results)
        assert not any(r["player_id"] == id_a for r in results)  # self excluded

        # 3. View public profile of Player B
        pub_res = client.get(f"/api/players/{id_b}/public-profile", headers=headers_a)
        assert pub_res.status_code == 200
        assert pub_res.json()["display_name"] == "ApiPlayerB"
        assert pub_res.json()["relationship_state"] == "none"

        # 4. Player A sends friend request to Player B
        send_res = client.post("/api/friends/requests", json={"recipient_id": id_b}, headers=headers_a)
        assert send_res.status_code == 200
        req_id = send_res.json()["request_id"]
        assert send_res.json()["status"] == "pending"

        # 5. Player B lists incoming requests
        inc_res = client.get("/api/friends/requests/incoming", headers=headers_b)
        assert inc_res.status_code == 200
        assert len(inc_res.json()["incoming_requests"]) == 1
        assert inc_res.json()["incoming_requests"][0]["request_id"] == req_id

        # 6. Player B accepts request
        acc_res = client.post(f"/api/friends/requests/{req_id}/accept", headers=headers_b)
        assert acc_res.status_code == 200
        assert acc_res.json()["status"] == "accepted"

        # 7. Both check friends list
        f_res_a = client.get("/api/friends", headers=headers_a)
        assert f_res_a.status_code == 200
        assert len(f_res_a.json()["friends"]) == 1
        assert f_res_a.json()["friends"][0]["player_id"] == id_b

        f_res_b = client.get("/api/friends", headers=headers_b)
        assert f_res_b.status_code == 200
        assert len(f_res_b.json()["friends"]) == 1
        assert f_res_b.json()["friends"][0]["player_id"] == id_a

        # 8. Player A removes friend
        del_res = client.delete(f"/api/friends/{id_b}", headers=headers_a)
        assert del_res.status_code == 200
        assert del_res.json()["removed"] is True

        assert len(client.get("/api/friends", headers=headers_a).json()["friends"]) == 0

        # 9. Player B blocks Player A
        block_res = client.post("/api/blocks", json={"target_id": id_a}, headers=headers_b)
        assert block_res.status_code == 200

        # 10. Player A search cannot see Player B anymore
        search_after_block = client.get("/api/players/search?query=ApiPlayerB", headers=headers_a)
        assert len(search_after_block.json()["results"]) == 0

        # 11. Player B unblocks Player A
        unblock_res = client.delete(f"/api/blocks/{id_a}", headers=headers_b)
        assert unblock_res.status_code == 200
        assert unblock_res.json()["unblocked"] is True

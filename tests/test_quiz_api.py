from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from quizverse.persistence.db import Database
from quizverse.persistence.models import CapabilityGrant
from quizverse.realtime import create_app


def migrate(database_url: str, project_root: Path) -> None:
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(Config(str(project_root / "alembic.ini")), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture
def api_client(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "api.sqlite3"
    database_url = f"sqlite:///{db_path.as_posix()}"
    migrate(database_url, project_root)
    database = Database(database_url)
    app = create_app(database=database)
    try:
        with TestClient(app) as client:
            yield client, database
    finally:
        database.close()


def owner_capability(client: TestClient) -> tuple[str, dict[str, str]]:
    response = client.post("/api/quiz-owners")
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    capability = response.json()["capability"]
    return capability, {"Authorization": f"Bearer {capability}"}


def sample_question(prompt: str = "Which planet is known as the Red Planet?") -> dict:
    return {
        "prompt": prompt,
        "options": ["Venus", "Mars", "Jupiter", "Mercury"],
        "correct_option_index": 1,
        "explanation": "Mars appears red because of iron oxide.",
        "category": "science",
        "difficulty": "easy",
        "time_limit_seconds": 15,
    }


def create_quiz(
    client: TestClient,
    headers: dict[str, str],
    *,
    title: str = "Planet facts",
    visibility: str = "private",
    status: int = 201,
) -> dict:
    response = client.post(
        "/api/quizzes",
        headers=headers,
        json={
            "title": title,
            "description": "A small test quiz.",
            "category": "science",
            "difficulty": "medium",
            "visibility": visibility,
            "default_time_limit_seconds": 15,
            "questions": [sample_question()],
        },
    )
    assert response.status_code == status, response.text
    return response.json()


def test_quiz_crud_publish_authorization_and_answer_safe_reads(api_client) -> None:
    client, _database = api_client
    _owner_token, owner = owner_capability(client)
    _other_token, other = owner_capability(client)
    draft = create_quiz(client, owner, visibility="public")
    quiz_id = draft["id"]
    assert draft["status"] == "draft"
    assert draft["questions"][0]["correct_option_index"] == 1
    assert client.get(f"/api/quizzes/{quiz_id}").status_code == 404
    assert client.get(f"/api/quizzes/{quiz_id}", headers=other).status_code == 404

    assert client.patch(
        f"/api/quizzes/{quiz_id}",
        headers=other,
        json={"expected_version": draft["version"], "title": "Hijack"},
    ).status_code == 404
    assert client.post(f"/api/quizzes/{quiz_id}/publish", headers=other).status_code == 404
    assert client.delete(f"/api/quizzes/{quiz_id}", headers=other).status_code == 404

    updated = client.patch(
        f"/api/quizzes/{quiz_id}",
        headers=owner,
        json={"expected_version": draft["version"], "title": "Solar system"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Solar system"
    assert updated.json()["version"] == draft["version"] + 1
    assert client.patch(
        f"/api/quizzes/{quiz_id}",
        headers=owner,
        json={"expected_version": draft["version"], "title": "Stale edit"},
    ).json()["error"]["code"] == "version_conflict"

    published = client.post(f"/api/quizzes/{quiz_id}/publish", headers=owner)
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    public_response = client.get(f"/api/quizzes/{quiz_id}")
    assert public_response.status_code == 200
    assert public_response.headers["cache-control"] == "no-store"
    public_view = public_response.json()
    assert "correct_option_index" not in public_view["questions"][0]
    assert "explanation" not in public_view["questions"][0]
    owner_view = client.get(f"/api/quizzes/{quiz_id}", headers=owner).json()
    assert owner_view["questions"][0]["correct_option_index"] == 1
    assert owner_view["questions"][0]["explanation"].startswith("Mars appears")
    assert client.post(f"/api/quizzes/{quiz_id}/unpublish", headers=owner).json()["status"] == "draft"
    assert client.get(f"/api/quizzes/{quiz_id}").status_code == 404


def test_quiz_question_update_replaces_question_content_and_order(api_client) -> None:
    client, _database = api_client
    _owner_token, owner = owner_capability(client)
    quiz = create_quiz(client, owner)
    updated = client.patch(
        f"/api/quizzes/{quiz['id']}",
        headers=owner,
        json={
            "expected_version": quiz["version"],
            "questions": [
                sample_question("Second prompt"),
                sample_question("First prompt"),
            ],
        },
    )

    assert updated.status_code == 200, updated.text
    assert [question["prompt"] for question in updated.json()["questions"]] == [
        "Second prompt",
        "First prompt",
    ]
    assert updated.json()["version"] == quiz["version"] + 1


def test_private_shares_are_scoped_revocable_expiring_and_not_edit_grants(api_client) -> None:
    client, database = api_client
    _owner_token, owner = owner_capability(client)
    quiz = create_quiz(client, owner)
    quiz_id = quiz["id"]
    assert client.post(f"/api/quizzes/{quiz_id}/publish", headers=owner).status_code == 200
    private_view = client.get(f"/api/quizzes/{quiz_id}")
    assert private_view.status_code == 404

    created_share = client.post(
        f"/api/quizzes/{quiz_id}/shares", headers=owner, json={}
    )
    assert created_share.status_code == 201
    assert created_share.headers["cache-control"] == "no-store"
    share = created_share.json()
    read_headers = {"Authorization": f"Bearer {share['capability']}"}
    readable = client.get(f"/api/quizzes/{quiz_id}", headers=read_headers)
    assert readable.status_code == 200
    assert "correct_option_index" not in readable.json()["questions"][0]
    assert client.patch(
        f"/api/quizzes/{quiz_id}",
        headers=read_headers,
        json={"expected_version": quiz["version"], "title": "Read grant edits"},
    ).status_code == 404

    second = create_quiz(client, owner, title="Second private quiz")
    assert client.post(f"/api/quizzes/{second['id']}/publish", headers=owner).status_code == 200
    assert client.get(f"/api/quizzes/{second['id']}", headers=read_headers).status_code == 404

    revoked = client.delete(f"/api/quizzes/{quiz_id}/shares/{share['grant_id']}", headers=owner)
    assert revoked.status_code == 204
    assert client.get(f"/api/quizzes/{quiz_id}", headers=read_headers).status_code == 404

    expiring = client.post(
        f"/api/quizzes/{quiz_id}/shares",
        headers=owner,
        json={"expires_in_seconds": 60},
    )
    assert expiring.status_code == 201
    expiring_data = expiring.json()
    with database.sessions.begin() as session:
        grant = session.get(CapabilityGrant, expiring_data["grant_id"])
        assert grant is not None
        from datetime import UTC, datetime, timedelta

        grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    expired_headers = {"Authorization": f"Bearer {expiring_data['capability']}"}
    assert client.get(f"/api/quizzes/{quiz_id}", headers=expired_headers).status_code == 404


def test_drafts_cannot_be_shared_and_invalid_capability_errors_do_not_echo_tokens(api_client) -> None:
    client, _database = api_client
    owner_token, owner = owner_capability(client)
    draft = create_quiz(client, owner)
    response = client.post(f"/api/quizzes/{draft['id']}/shares", headers=owner, json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "quiz_not_shareable"
    unsupported_edit = client.post(
        f"/api/quizzes/{draft['id']}/shares", headers=owner, json={"scope": "edit"}
    )
    assert unsupported_edit.status_code == 422

    invalid = client.get(
        f"/api/quizzes/{draft['id']}", headers={"Authorization": f"Bearer {owner_token}"}
    )
    assert invalid.status_code == 200
    assert invalid.json()["questions"][0]["correct_option_index"] == 1
    denied = client.get(
        f"/api/quizzes/{draft['id']}", headers={"Authorization": "Bearer " + "x" * 44}
    )
    assert denied.status_code == 404
    rejected = client.post(
        "/api/quizzes",
        headers=owner,
        json={"title": "bad", "category": "science", "capability": owner_token},
    )
    assert rejected.status_code == 422
    assert owner_token not in rejected.text


def test_strict_validation_and_public_library_filters(api_client) -> None:
    client, _database = api_client
    _, owner = owner_capability(client)
    assert client.post(
        "/api/quizzes", json={"title": "No owner", "category": "science"}
    ).status_code == 401

    empty = client.post(
        "/api/quizzes", headers=owner, json={"title": "Empty draft", "category": "science"}
    )
    assert empty.status_code == 201
    empty_publish = client.post(f"/api/quizzes/{empty.json()['id']}/publish", headers=owner)
    assert empty_publish.status_code == 422
    assert empty_publish.json()["error"]["code"] == "quiz_not_publishable"

    invalid_options = sample_question()
    invalid_options["options"] = ["same", "same", "other", "last"]
    bad = client.post(
        "/api/quizzes",
        headers=owner,
        json={"title": "Bad choices", "category": "science", "questions": [invalid_options]},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_quiz"

    bad_extra = client.post(
        "/api/quizzes",
        headers=owner,
        json={"title": "Bad extra", "category": "science", "score": 9999},
    )
    assert bad_extra.status_code == 422
    assert bad_extra.json()["error"]["code"] == "invalid_request"

    public = create_quiz(client, owner, title="History of Mars", visibility="public")
    assert client.get(f"/api/quizzes/{public['id']}").status_code == 404  # still a draft
    assert client.post(f"/api/quizzes/{public['id']}/publish", headers=owner).status_code == 200
    listed = client.get("/api/quizzes", params={"category": "science", "q": "Mars"})
    assert listed.status_code == 200
    assert [quiz["id"] for quiz in listed.json()["quizzes"]] == [public["id"]]
    assert "correct_option_index" not in listed.json()["quizzes"][0]["questions"][0]


def test_public_duplication_attribution_and_soft_delete(api_client) -> None:
    client, _database = api_client
    _owner_token, owner = owner_capability(client)
    public = create_quiz(client, owner, title="Open knowledge", visibility="public")
    assert client.post(f"/api/quizzes/{public['id']}/publish", headers=owner).status_code == 200
    duplicate = client.post(f"/api/quizzes/{public['id']}/duplicate", headers=owner)
    assert duplicate.status_code == 201
    duplicate_data = duplicate.json()
    assert duplicate_data["status"] == "draft"
    assert duplicate_data["visibility"] == "private"
    assert public["id"] in duplicate_data["description"]
    assert duplicate_data["questions"][0]["correct_option_index"] == 1
    assert duplicate_data["questions"][0]["id"] != public["questions"][0]["id"]

    private = create_quiz(client, owner, title="Private source")
    assert client.post(f"/api/quizzes/{private['id']}/duplicate", headers=owner).status_code == 404
    assert client.delete(f"/api/quizzes/{public['id']}", headers=owner).status_code == 204
    assert client.get(f"/api/quizzes/{public['id']}").status_code == 404
    assert public["id"] not in [item["id"] for item in client.get("/api/quizzes").json()["quizzes"]]


def test_owner_list_and_missing_or_invalid_owner_capabilities(api_client) -> None:
    client, _database = api_client
    _, owner = owner_capability(client)
    create_quiz(client, owner)
    assert client.get("/api/quizzes/mine").status_code == 401
    assert client.get("/api/quizzes/mine", headers={"Authorization": "Bearer " + "x" * 44}).status_code == 401
    owned = client.get("/api/quizzes/mine", headers=owner)
    assert owned.status_code == 200
    assert len(owned.json()["quizzes"]) == 1
    assert owned.json()["quizzes"][0]["questions"][0]["correct_option_index"] == 1


def test_owner_creation_rate_limit_is_scoped_and_returns_no_store_headers(api_client) -> None:
    client, _database = api_client
    created = [client.post("/api/quiz-owners") for _ in range(5)]
    assert all(response.status_code == 201 for response in created)
    assert all(response.headers["cache-control"] == "no-store" for response in created)

    limited = client.post("/api/quiz-owners")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "owner_creation_rate_limited"
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    assert limited.headers["cache-control"] == "no-store"

    # The limiter protects only anonymous owner creation, not unrelated reads.
    public_list = client.get("/api/quizzes")
    assert public_list.status_code == 200
    assert public_list.headers["cache-control"] == "no-store"


def test_unauthorized_owner_cannot_call_any_mutation_route(api_client) -> None:
    client, _database = api_client
    _owner_token, owner = owner_capability(client)
    _attacker_token, attacker = owner_capability(client)
    public_quiz = create_quiz(client, owner, visibility="public")
    quiz_id = public_quiz["id"]
    assert client.post(f"/api/quizzes/{quiz_id}/publish", headers=owner).status_code == 200
    private_quiz = create_quiz(client, owner, title="Owner-only source")
    share_response = client.post(f"/api/quizzes/{quiz_id}/shares", headers=owner, json={})
    assert share_response.status_code == 201
    grant_id = share_response.json()["grant_id"]

    assert client.patch(
        f"/api/quizzes/{quiz_id}",
        headers=attacker,
        json={"expected_version": public_quiz["version"] + 1, "title": "Unauthorized"},
    ).status_code == 404
    assert client.post(f"/api/quizzes/{quiz_id}/publish", headers=attacker).status_code == 404
    assert client.post(f"/api/quizzes/{quiz_id}/unpublish", headers=attacker).status_code == 404
    assert client.post(f"/api/quizzes/{quiz_id}/duplicate").status_code == 401
    # Public duplication is intentionally available to any owner; private sources
    # remain owner-only and cannot be duplicated by another valid owner.
    assert client.post(
        f"/api/quizzes/{private_quiz['id']}/duplicate", headers=attacker
    ).status_code == 404
    assert client.delete(f"/api/quizzes/{quiz_id}", headers=attacker).status_code == 404
    assert client.post(
        f"/api/quizzes/{quiz_id}/shares", headers=attacker, json={}
    ).status_code == 404
    assert client.delete(
        f"/api/quizzes/{quiz_id}/shares/{grant_id}", headers=attacker
    ).status_code == 404
    assert client.get(
        f"/api/quizzes/{quiz_id}",
        headers={"Authorization": f"Bearer {share_response.json()['capability']}"},
    ).status_code == 200


def test_capability_bearing_api_responses_and_errors_are_not_cacheable(api_client) -> None:
    client, _database = api_client
    owner_response = client.post("/api/quiz-owners")
    assert owner_response.status_code == 201
    assert owner_response.headers["cache-control"] == "no-store"
    owner = owner_response.json()["capability"]
    owner_headers = {"Authorization": f"Bearer {owner}"}

    invalid_request = client.post(
        "/api/quizzes", headers=owner_headers, json={"unknown": "field"}
    )
    assert invalid_request.status_code == 422
    assert invalid_request.headers["cache-control"] == "no-store"
    assert owner not in invalid_request.text

    missing_capability = client.get("/api/quizzes/mine")
    assert missing_capability.status_code == 401
    assert missing_capability.headers["cache-control"] == "no-store"

    quiz = create_quiz(client, owner_headers)
    assert client.post(f"/api/quizzes/{quiz['id']}/publish", headers=owner_headers).status_code == 200
    share_response = client.post(f"/api/quizzes/{quiz['id']}/shares", headers=owner_headers, json={})
    assert share_response.status_code == 201
    assert share_response.headers["cache-control"] == "no-store"
    assert share_response.json()["capability"] not in share_response.headers.get("location", "")
    denied = client.delete(
        f"/api/quizzes/{quiz['id']}/shares/{share_response.json()['grant_id']}",
        headers={"Authorization": "Bearer " + "x" * 44},
    )
    assert denied.status_code == 404
    assert denied.headers["cache-control"] == "no-store"

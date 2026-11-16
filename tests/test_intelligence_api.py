"""Comprehensive API integration and security tests for AI Intelligence Engine."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from quizverse.persistence.db import Database
from quizverse.persistence.player_identity import PlayerIdentityService
from quizverse.realtime import create_app


@pytest.fixture
def test_db(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'intel-api-test.sqlite3').as_posix()}"
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
def app(test_db: Database):
    return create_app(database=test_db)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def identity_service(test_db: Database) -> PlayerIdentityService:
    return PlayerIdentityService(test_db)


@pytest.fixture
def player_one(identity_service: PlayerIdentityService):
    claim = identity_service.create("IntelPlayerOne")
    return {"id": claim.player_id, "token": claim.token}


@pytest.fixture
def player_two(identity_service: PlayerIdentityService):
    claim = identity_service.create("IntelPlayerTwo")
    return {"id": claim.player_id, "token": claim.token}


def test_intelligence_health(client: TestClient):
    response = client.get("/api/intelligence/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "capabilities" in data


def test_question_analysis_api(client: TestClient):
    payload = {
        "prompt": "What is the primary function of chlorophyll in photosynthesis?",
        "options": [
            "Absorbing light energy",
            "Synthesizing starch polymers",
            "Transporting cellular water",
            "Fixing atmospheric nitrogen",
        ],
        "correct_option_index": 0,
        "category": "Biology",
        "difficulty": "medium",
        "explanation": "Chlorophyll absorbs photon wavelengths to power the light-dependent reactions.",
    }
    response = client.post("/api/intelligence/questions/analyze", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["quality_score"] >= 80.0
    assert "clarity_score" in data
    assert "consistency_score" in data


def test_answer_validation_api(client: TestClient):
    payload = {
        "prompt": "What is 20 * 4?",
        "options": ["60", "70", "80", "90"],
        "correct_option_index": 2,
        "explanation": "20 * 4 = 80.",
    }
    response = client.post("/api/intelligence/questions/validate-answer", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["is_valid"] is True
    assert data["arithmetic_verified"] is True


def test_check_duplicate_api(client: TestClient):
    payload = {
        "prompt": "What is the capital of France?",
    }
    response = client.post("/api/intelligence/questions/check-duplicate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "duplicate_status" in data
    assert "similarity_score" in data


def test_unauthenticated_requests_rejected(client: TestClient):
    # Without Bearer token, private player intelligence endpoints must return 401
    assert client.get("/api/intelligence/overview").status_code == 401
    assert client.get("/api/intelligence/weaknesses").status_code == 401
    assert client.get("/api/intelligence/difficulty").status_code == 401
    assert client.get("/api/intelligence/learning-paths").status_code == 401
    assert client.post("/api/intelligence/learning-paths/generate", json={}).status_code == 401
    assert client.get("/api/intelligence/recommendations").status_code == 401
    assert client.get("/api/intelligence/performance").status_code == 401
    assert client.post("/api/intelligence/reports/generate", json={}).status_code == 401
    assert client.get("/api/intelligence/reports").status_code == 401


def test_authenticated_overview_and_difficulty(client: TestClient, player_one):
    headers = {"Authorization": f"Bearer {player_one['token']}"}
    response = client.get("/api/intelligence/overview", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "mastery_level" in data["summary"]

    diff_res = client.get("/api/intelligence/difficulty", headers=headers)
    assert diff_res.status_code == 200
    diff_data = diff_res.json()
    assert "recommended_difficulty" in diff_data
    assert "confidence" in diff_data


def test_learning_path_lifecycle(client: TestClient, player_one, player_two):
    p1_headers = {"Authorization": f"Bearer {player_one['token']}"}
    p2_headers = {"Authorization": f"Bearer {player_two['token']}"}

    # Generate a path for player one
    gen_res = client.post(
        "/api/intelligence/learning-paths/generate",
        json={"regenerate": True},
        headers=p1_headers,
    )
    assert gen_res.status_code == 201
    path = gen_res.json()
    assert path["status"] == "active"
    assert len(path["steps"]) == 4

    step_1_id = path["steps"][0]["id"]
    path_id = path["id"]

    # Player 2 tries to complete Player 1's step -> Not found / Unauthorized
    p2_attempt = client.post(
        f"/api/intelligence/learning-paths/{path_id}/steps/{step_1_id}/complete",
        headers=p2_headers,
    )
    assert p2_attempt.status_code in (403, 404)

    # Player 1 completes step 1 -> Success
    p1_complete = client.post(
        f"/api/intelligence/learning-paths/{path_id}/steps/{step_1_id}/complete",
        headers=p1_headers,
    )
    assert p1_complete.status_code == 200
    updated_path = p1_complete.json()
    assert updated_path["steps"][0]["is_completed"] is True
    assert updated_path["progress_pct"] == 25.0


def test_learning_report_generation_and_retrieval(client: TestClient, player_one, player_two):
    p1_headers = {"Authorization": f"Bearer {player_one['token']}"}
    p2_headers = {"Authorization": f"Bearer {player_two['token']}"}

    gen_res = client.post(
        "/api/intelligence/reports/generate",
        json={"time_window": "recent"},
        headers=p1_headers,
    )
    assert gen_res.status_code == 201
    report = gen_res.json()
    assert "summary" in report
    assert "data" in report
    report_id = report["id"]

    # List reports
    list_res = client.get("/api/intelligence/reports", headers=p1_headers)
    assert list_res.status_code == 200
    reports = list_res.json()
    assert any(r["id"] == report_id for r in reports)

    # Get single report detail
    detail_res = client.get(f"/api/intelligence/reports/{report_id}", headers=p1_headers)
    assert detail_res.status_code == 200
    assert detail_res.json()["id"] == report_id

    # Player 2 cannot access Player 1's report -> 404 Not Found
    p2_detail = client.get(f"/api/intelligence/reports/{report_id}", headers=p2_headers)
    assert p2_detail.status_code == 404


def test_recommendations_and_performance(client: TestClient, player_one):
    headers = {"Authorization": f"Bearer {player_one['token']}"}

    recs_res = client.get("/api/intelligence/recommendations", headers=headers)
    assert recs_res.status_code == 200
    assert isinstance(recs_res.json(), list)

    perf_res = client.get("/api/intelligence/performance?time_window=recent", headers=headers)
    assert perf_res.status_code == 200
    perf = perf_res.json()
    assert "overall_accuracy_pct" in perf
    assert "total_attempts" in perf


def test_models_and_bank_summary(client: TestClient):
    models_res = client.get("/api/intelligence/models")
    assert models_res.status_code == 200
    models_data = models_res.json()
    assert "active_provider" in models_data

    bank_res = client.get("/api/intelligence/bank/summary")
    assert bank_res.status_code == 200
    bank_data = bank_res.json()
    assert "total_questions" in bank_data
    assert bank_data["total_questions"] > 0

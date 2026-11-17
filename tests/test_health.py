import asyncio

import httpx

from quizverse.app import app


def test_health_endpoint_reports_ready() -> None:
    async def request_health() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health")

    response = asyncio.run(request_health())

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "service": "quizverse-live",
        "pending_match_history": 0,
        "retrying_match_history": 0,
    }


def test_ready_endpoint_reports_ready() -> None:
    async def request_ready() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/ready")

    response = asyncio.run(request_ready())
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    assert data["service"] == "quizverse-live"
    assert data["database"]["connected"] is True

from httpx import ASGITransport, AsyncClient

from src.main import app


async def test_chat_endpoint_returns_echo() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/chat",
            json={"session_id": "s-123", "message": "Ciao!"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "s-123"
    assert data["reply"] == "Echo: Ciao!"
    assert data["tokens_used"] == 10
    assert data["cost_eur"] == 0.0001
    assert data["model_used"] == "dummy"
    assert data["tool_calls"] == []
    assert "created_at" in data


async def test_chat_empty_message_returns_422() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/chat",
            json={"session_id": "s-123", "message": ""},
        )
    assert response.status_code == 422
    data = response.json()
    assert data["error"] == "VALIDATION_ERROR"
    assert data["status"] == 422
    assert "details" in data


async def test_chat_validation_error_has_uniform_shape() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/chat",
            json={"session_id": "s-123", "message": "a" * 2001},
        )
    assert response.status_code == 422
    data = response.json()
    assert "timestamp" in data
    assert data["path"] == "/api/ai/chat"

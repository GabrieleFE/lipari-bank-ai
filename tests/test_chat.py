from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from src.main import app


async def test_chat_new_session_returns_uuid_and_echo() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/chat",
            json={"session_id": "new", "message": "Ciao!"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] != "new"
    UUID(data["session_id"])
    assert data["reply"] == "Echo: Ciao!"
    assert data["tokens_used"] == 10
    assert data["cost_eur"] == 0.0001
    assert data["model_used"] == "dummy"
    assert data["tool_calls"] == []
    assert "created_at" in data


async def test_chat_continues_existing_session() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/ai/chat",
            json={"session_id": "new", "message": "Primo messaggio"},
        )
        assert first.status_code == 200
        session_id = first.json()["session_id"]

        second = await client.post(
            "/api/ai/chat",
            json={"session_id": session_id, "message": "Secondo messaggio"},
        )
    assert second.status_code == 200
    data = second.json()
    assert data["session_id"] == session_id
    assert data["reply"] == "Echo: Secondo messaggio"


async def test_chat_unknown_session_returns_404() -> None:
    unknown = str(uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/chat",
            json={"session_id": unknown, "message": "Chi sei?"},
        )
    assert response.status_code == 404
    assert response.json()["error"] == "CHAT_SESSION_NOT_FOUND"


async def test_chat_empty_message_returns_422() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/chat",
            json={"session_id": "new", "message": ""},
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
            json={"session_id": "new", "message": "a" * 2001},
        )
    assert response.status_code == 422
    data = response.json()
    assert "timestamp" in data
    assert data["path"] == "/api/ai/chat"

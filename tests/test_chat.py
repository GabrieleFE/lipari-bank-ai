import json
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.main import app
from tests.fakes import FakeLLMProvider


async def test_chat_new_session_returns_uuid_and_llm_reply(
    fake_llm_provider: FakeLLMProvider,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/chat",
            json={"session_id": "new", "message": "Ciao!"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] != "new"
    UUID(data["session_id"])
    assert data["reply"] == "Fake reply: Ciao!"
    assert data["tokens_used"] == 10
    assert data["cost_eur"] == 0.0001
    assert data["model_used"] == "fake-gpt"
    assert "created_at" in data


async def test_chat_multi_turn_remembers_history(fake_llm_provider: FakeLLMProvider) -> None:
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
    assert data["reply"] == "Fake reply: Secondo messaggio"

    # Il provider ha ricevuto l'history: system + user/assistant dei turni precedenti
    last_call = fake_llm_provider.calls[-1]
    roles = [m["role"] for m in last_call]
    assert roles.count("user") == 2  # primo + secondo messaggio
    assert "assistant" in roles  # la risposta finta del primo turno rientra nel contesto
    contents = [m["content"] for m in last_call if m["role"] == "user"]
    assert contents == ["Primo messaggio", "Secondo messaggio"]


async def test_chat_unknown_session_returns_404(fake_llm_provider: FakeLLMProvider) -> None:
    unknown = str(uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/chat",
            json={"session_id": unknown, "message": "Chi sei?"},
        )
    assert response.status_code == 404
    assert response.json()["error"] == "CHAT_SESSION_NOT_FOUND"


async def test_chat_empty_message_returns_422(fake_llm_provider: FakeLLMProvider) -> None:
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


async def test_chat_validation_error_has_uniform_shape(
    fake_llm_provider: FakeLLMProvider,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/chat",
            json={"session_id": "new", "message": "a" * 2001},
        )
    assert response.status_code == 422
    data = response.json()
    assert "timestamp" in data
    assert data["path"] == "/api/ai/chat"


async def test_chat_budget_reached_returns_429(
    fake_llm_provider: FakeLLMProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_daily_cost_eur", 0.0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/chat",
            json={"session_id": "new", "message": "Ciao!"},
        )
    assert response.status_code == 429
    assert response.json()["error"] == "RATE_LIMIT"
    assert "retry_after" in response.json()
    assert fake_llm_provider.calls == []  # nessuna chiamata LLM avvenuta


async def test_chat_stream_returns_sse(fake_llm_provider: FakeLLMProvider) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/chat/stream",
            json={"session_id": "new", "message": "Ciao"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "data: [DONE]" in body
    # I chunk arrivano come SSE con JSON: data: {"delta": "..."}
    assert 'data: {"delta": "Fake "' in body
    # Il testo completo e' la concatenazione dei chunk
    deltas = []
    for line in body.splitlines():
        if line.startswith("data: ") and "delta" in line:
            deltas.append(json.loads(line[6:])["delta"])
    assert "".join(deltas) == "Fake reply: Ciao"

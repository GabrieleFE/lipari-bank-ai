from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

import src.main as main_module
from src.config import ConfigurationError, Settings, load_settings, secret_is_configured, settings
from src.main import app


def make_settings(
    *,
    openai_key: str | None = "test-openai-key",
    anthropic_key: str | None = "test-anthropic-key",
) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@localhost:5432/test",
        openai_api_key=SecretStr(openai_key) if openai_key is not None else None,
        anthropic_api_key=SecretStr(anthropic_key) if anthropic_key is not None else None,
        jwt_secret=SecretStr("j" * 48),
    )


async def test_health_endpoint() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    payload = response.json()
    assert payload["app_name"] == settings.app_name
    assert payload["environment"] == settings.environment
    assert payload["credentials"] == {
        "openai_configured": secret_is_configured(settings.openai_api_key),
        "anthropic_configured": secret_is_configured(settings.anthropic_api_key),
        "missing": [
            name
            for name, configured in (
                ("OPENAI_API_KEY", secret_is_configured(settings.openai_api_key)),
                ("ANTHROPIC_API_KEY", secret_is_configured(settings.anthropic_api_key)),
            )
            if not configured
        ],
    }
    assert response.status_code == (200 if payload["status"] == "UP" else 503)


async def test_health_reports_missing_credentials_without_leaking_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "settings", make_settings(openai_key=None, anthropic_key=None))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "DEGRADED"
    assert response.json()["credentials"] == {
        "openai_configured": False,
        "anthropic_configured": False,
        "missing": ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
    }
    assert "test-openai-key" not in response.text
    assert "test-anthropic-key" not in response.text


def test_missing_required_setting_has_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("APP_NAME=LipariBank AI\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:
        load_settings(str(env_file))
    assert "DATABASE_URL: è obbligatoria" in str(error.value)
    assert "JWT_SECRET: è obbligatoria" in str(error.value)


def test_invalid_setting_type_names_field_and_expected_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAX_TOKENS_PER_REQUEST", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/test\n"
        f"JWT_SECRET={'j' * 48}\n"
        "MAX_TOKENS_PER_REQUEST=molti\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as error:
        load_settings(str(env_file))
    assert "MAX_TOKENS_PER_REQUEST: deve essere un numero intero" in str(error.value)


async def test_request_id_header_present() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert "x-request-id" in response.headers
    assert response.headers["x-request-id"]


async def test_cors_headers_present() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health", headers={"Origin": "http://localhost:4200"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:4200"

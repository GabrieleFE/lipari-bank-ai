from httpx import ASGITransport, AsyncClient

from src.main import app


async def test_health_endpoint() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "UP"
    assert data["app_name"] == "LipariBank AI"
    assert "timestamp" in data
    assert "version" in data

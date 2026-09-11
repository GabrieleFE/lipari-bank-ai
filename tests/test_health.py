from httpx import ASGITransport, AsyncClient

from src.main import app


async def test_health_endpoint() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


async def test_request_id_header_present() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert "x-request-id" in response.headers
    assert response.headers["x-request-id"]


async def test_cors_headers_present() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health", headers={"Origin": "http://localhost:4200"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:4200"

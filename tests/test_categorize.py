from httpx import ASGITransport, AsyncClient

from src.main import app


async def test_categorize_utilities_keyword() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/categorize",
            json={"description": "Bonifico Enel Energia", "amount": 100},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["category"] == "UTILITIES"
    assert data["subcategory"] == "ENERGY"
    assert data["confidence"] == 0.92


async def test_categorize_groceries_keyword() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/categorize",
            json={"description": "Supermercato Coop", "amount": 45.5},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["category"] == "GROCERIES"
    assert data["subcategory"] == "SUPERMARKET"
    assert data["confidence"] == 0.85


async def test_categorize_unknown_falls_back_to_other() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/categorize",
            json={"description": "Rimborso random", "amount": 10},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["category"] == "OTHER"
    assert data["subcategory"] == "UNCATEGORIZED"
    assert data["confidence"] == 0.10


async def test_categorize_negative_amount_returns_422() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/categorize",
            json={"description": "Affitto", "amount": -10},
        )
    assert response.status_code == 422


async def test_categorize_invalid_currency_returns_422() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/categorize",
            json={"description": "Affitto", "amount": 10, "currency": "EUR-IT"},
        )
    assert response.status_code == 422

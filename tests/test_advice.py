from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.api.advice import get_embedding_client
from src.config import settings
from src.db.models import DocumentChunk
from src.main import app
from src.services.retrieval_service import RetrievalService
from tests.fakes import FakeEmbeddingClient, FakeLLMProvider

CONTENT_MATCH = "La commissione per il bonifico istantaneo SEPA è di 1,00 euro."


@pytest.fixture(autouse=True)
async def clean_document_chunks() -> AsyncIterator[None]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool, echo=False)
    async with engine.begin() as conn:
        await conn.execute(delete(DocumentChunk))
    try:
        yield
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(DocumentChunk))
        await engine.dispose()


@pytest.fixture()
def fake_embedding_client() -> FakeEmbeddingClient:
    client = FakeEmbeddingClient()
    app.dependency_overrides[get_embedding_client] = lambda: client
    yield client
    app.dependency_overrides.pop(get_embedding_client, None)


async def _document_chunk_count() -> int:
    engine = create_async_engine(settings.database_url, poolclass=NullPool, echo=False)
    async with engine.begin() as conn:
        result = await conn.execute(select(func.count()).select_from(DocumentChunk))
        count = result.scalar_one()
    await engine.dispose()
    return count


async def test_ingest_creates_chunks_and_reingest_is_idempotent(
    fake_embedding_client: FakeEmbeddingClient,
) -> None:
    payload = {
        "document_id": "commissioni_bonifico",
        "content": CONTENT_MATCH,
        "metadata": {"title": "Commissioni"},
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/ai/documents/ingest", json=payload)
        second = await client.post("/api/ai/documents/ingest", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == {"chunk_count": 1, "embedding_dim": settings.embedding_dim}
    assert second.json()["chunk_count"] == 1
    assert await _document_chunk_count() == 1  # niente duplicati dopo il re-ingest
    assert fake_embedding_client.calls  # la chiamata batched è avvenuta


async def test_advice_returns_answer_with_citations(
    fake_embedding_client: FakeEmbeddingClient,
    fake_llm_provider: FakeLLMProvider,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/api/ai/documents/ingest",
            json={"document_id": "commissioni_bonifico", "content": CONTENT_MATCH},
        )
        response = await client.post(
            "/api/ai/advice",
            json={"question": CONTENT_MATCH},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["answer"].startswith("Fake reply:")
    assert data["tokens_used"] == 10
    assert data["cost_eur"] == 0.0001
    assert len(data["citations"]) == 1
    citation = data["citations"][0]
    assert citation["document_id"] == "commissioni_bonifico"
    assert citation["similarity"] > 0.99
    assert citation["chunk_id"]


async def test_advice_without_documents_returns_refusal(
    fake_embedding_client: FakeEmbeddingClient,
    fake_llm_provider: FakeLLMProvider,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/advice",
            json={"question": "Quali sono le regole sugli investimenti ESG?"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Non ho informazioni su questa domanda."
    assert data["citations"] == []
    assert data["tokens_used"] == 0
    assert fake_llm_provider.calls == []  # nessuna generazione se non c'è retrieval


async def test_advice_short_question_returns_422(
    fake_embedding_client: FakeEmbeddingClient,
    fake_llm_provider: FakeLLMProvider,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/ai/advice", json={"question": "ciao"})
    assert response.status_code == 422
    assert response.json()["error"] == "VALIDATION_ERROR"


async def test_retrieval_orders_by_similarity_and_filters_below_threshold(
    fake_embedding_client: FakeEmbeddingClient,
) -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    other = "Il canone mensile del conto standard è di 6,50 euro."
    async with session_factory() as session:
        session.add_all(
            [
                DocumentChunk(
                    document_id="doc-a",
                    chunk_index=0,
                    content=CONTENT_MATCH,
                    embedding=await fake_embedding_client.embed_one(CONTENT_MATCH),
                    chunk_metadata={},
                ),
                DocumentChunk(
                    document_id="doc-b",
                    chunk_index=0,
                    content=other,
                    embedding=await fake_embedding_client.embed_one(other),
                    chunk_metadata={},
                ),
            ]
        )
        await session.commit()

    async with session_factory() as session:
        service = RetrievalService(session, fake_embedding_client)
        results = await service.retrieve(CONTENT_MATCH, top_k=5, min_similarity=0.6)

    assert [r.document_id for r in results] == ["doc-a"]
    assert results[0].similarity > 0.99
    await engine.dispose()

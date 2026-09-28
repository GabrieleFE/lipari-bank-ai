from collections.abc import AsyncGenerator, Generator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.db.session import get_db
from src.llm.factory import get_llm_provider
from src.main import app
from tests.fakes import FakeLLMProvider


@pytest.fixture(autouse=True)
async def db_engine_per_test() -> AsyncGenerator[None]:
    """Engine dedicato (NullPool) per ogni test: niente connessioni condivise tra event loop."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.fixture(autouse=True)
def fresh_idempotency_store() -> Generator[None, None, None]:
    """Un registro vuoto per ogni test.

    Il registro di idempotenza vive nel processo, quindi senza questo un test che
    usa `Idempotency-Key: k1` lascia `k1` occupato e il test successivo con la stessa
    chiave riceverebbe un `replayed: true` che non ha chiesto. Fallo nascosto.
    """
    from src.api.movements import get_idempotency_store

    store = get_idempotency_store()
    store.clear()
    yield
    store.clear()


@pytest.fixture()
def fake_llm_provider() -> Generator[FakeLLMProvider, None, None]:
    """Sostituisce LLMProvider (chat) con un fake.

    `/api/ai/categorize` non viene sovrascritto: il default e' la regola a parole
    chiave, che e' deterministica e non parla con nessuno. Se un test volesse
    provare il provider LLM, e' `build_categorizer(provider="llm")` che si
    sostituisce, non l'endpoint.
    """
    provider = FakeLLMProvider()
    app.dependency_overrides[get_llm_provider] = lambda: provider
    yield provider
    app.dependency_overrides.pop(get_llm_provider, None)

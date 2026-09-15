from collections.abc import AsyncGenerator, Generator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.api.categorize import get_categorize_service
from src.config import settings
from src.db.session import get_db
from src.llm.factory import get_llm_provider
from src.main import app
from tests.fakes import FakeCategorizeService, FakeLLMProvider


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


@pytest.fixture()
def fake_llm_provider() -> Generator[FakeLLMProvider, None, None]:
    """Sostituisce LLMProvider (chat) e CategorizeService (categorize) con dei fake."""
    provider = FakeLLMProvider()
    app.dependency_overrides[get_llm_provider] = lambda: provider
    app.dependency_overrides[get_categorize_service] = lambda: FakeCategorizeService()
    yield provider
    app.dependency_overrides.pop(get_llm_provider, None)
    app.dependency_overrides.pop(get_categorize_service, None)

from collections.abc import AsyncGenerator, Generator
from typing import NamedTuple

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.config import settings
from src.db.session import get_db
from src.llm.factory import get_llm_provider
from src.main import app
from tests.fakes import FakeLLMProvider


class DbHandle(NamedTuple):
    """Un database per test, con l'engine a disposizione per ascoltare le query.

    Il nome non comincia con `Test` perche' pytest raccolgerebbe la classe come
    se fosse un test e protesterebbe: `TestDb` non ha un `__init__` da test.
    """

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]


class QueryRecorder:
    """Conta le frasi che arrivano davvero al driver.

    Per la prova del giorno 3: "le query non crescono col numero di righe" non si
    dimostra guardando il codice, si conta. Tenere anche il testo delle query serve
    a controllare *quale* query e' stata mandata, che e' la parte che distingue un
    JOIN da un N+1: i due fanno lo stesso numero di query solo per caso.
    """

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.recording = False

    def on_statement(self, _conn: object, _cursor: object, statement: str, *_: object) -> None:
        if self.recording:
            self.statements.append(statement)

    @property
    def count(self) -> int:
        return len(self.statements)

    def start(self) -> None:
        self.statements.clear()
        self.recording = True

    def stop(self) -> None:
        self.recording = False

    def touching(self, table: str) -> int:
        return sum(1 for s in self.statements if table in s)


@pytest.fixture(autouse=True)
async def db_engine_per_test() -> AsyncGenerator[DbHandle, None]:
    """Engine dedicato (NullPool) per ogni test: niente connessioni condivise tra event loop."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                # Come `get_db`: se una richiesta muore, la transazione si annulla
                # qui e non perche' qualcuno si e' ricordato di chiamarla.
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield DbHandle(engine, session_factory)
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.fixture()
async def session(db_engine_per_test: DbHandle) -> AsyncGenerator[AsyncSession]:
    """Una sessione per il test, con la stessa dependency che usa l'app."""
    async with db_engine_per_test.session_factory() as s:
        yield s


@pytest.fixture()
def query_recorder(db_engine_per_test: DbHandle) -> Generator[QueryRecorder, None, None]:
    """Ascolta le query che l'engine di questo test manda al driver."""
    recorder = QueryRecorder()
    engine = db_engine_per_test.engine.sync_engine
    event.listen(engine, "before_cursor_execute", recorder.on_statement)
    try:
        yield recorder
    finally:
        event.remove(engine, "before_cursor_execute", recorder.on_statement)


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

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings

engine = create_async_engine(settings.database_url, echo=settings.debug, pool_pre_ping=True)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Sessione async per-request: aperta su ogni richiesta, chiusa al termine.

    Una sessione per richiesta, e `Depends(get_db)` in un solo punto: e' la
    dipendenza che FastAPI riusa per endpoint e provider dello stesso contesto,
    cosi' due funzioni che prendono la sessione nella stessa richiesta prendono
    la stessa transazione.

    Il `rollback()` e' scritto, non affidato alla chiusura. Chiudere una sessione
    con una transazione aperta la annulla gia' (`AsyncSession.close()` fa
    rollback), quindi il codice qui sotto sarebbe redundante: ma e' una riga che
    dice cosa succede quando una richiesta muore a meta', e la lezione del giorno
    e' esattamente che quella riga non e' una riga, e' tutta la transazione che
    tiene insieme le scritture. Se un domani qualcuno committa dentro un
    repository, questa riga da sola non lo salva piu': e' per questo che il
    commit sta nei servizi e si vede in un solo posto.
    """
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

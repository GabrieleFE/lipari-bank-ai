"""Misura il vantaggio dell'async: query in parallelo vs sequenziali.

Due scenari:
- A) micro-query `SELECT 1` (I/O rapidissimo, misura l'overhead)
- B) query con attesa reale (`pg_sleep 0.05`, simula il RTT di un DB/API reale)

Esempio:
    uv run python scripts/async_vs_sync.py
"""

import asyncio
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings

DOCS = Path(__file__).resolve().parent.parent / "docs" / "async-vs-sync-experiment.md"
POOL_SIZE = 100


async def run_query(session_factory: async_sessionmaker[AsyncSession], stmt: text) -> None:
    async with session_factory() as session:
        await session.execute(stmt)


async def run_sequential(
    session_factory: async_sessionmaker[AsyncSession], stmt: text, n: int
) -> float:
    start = time.perf_counter()
    for _ in range(n):
        await run_query(session_factory, stmt)
    return time.perf_counter() - start


async def run_parallel(
    session_factory: async_sessionmaker[AsyncSession], stmt: text, n: int
) -> float:
    start = time.perf_counter()
    await asyncio.gather(*(run_query(session_factory, stmt) for _ in range(n)))
    return time.perf_counter() - start


async def main() -> None:
    engine = create_async_engine(settings.database_url, pool_size=POOL_SIZE, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    for _ in range(10):  # warmup pool
        await run_query(session_factory, text("SELECT 1"))

    select_1 = text("SELECT 1")
    seq_a = await run_sequential(session_factory, select_1, n=100)
    par_a = await run_parallel(session_factory, select_1, n=100)

    sleep_query = text("SELECT pg_sleep(0.05)")
    seq_b = await run_sequential(session_factory, sleep_query, n=50)
    par_b = await run_parallel(session_factory, sleep_query, n=50)

    await engine.dispose()

    speedup_a = seq_a / par_a
    speedup_b = seq_b / par_b
    content = f"""# Async vs sync — esperimento

> Misura locale: query su PostgreSQL 16 in Docker (porta 5433), Windows 10, Python 3.12.
> `asyncio.gather` lancia le coroutine in parallelo; il tempo sequenziale e' la stima
> dell'equivalente "una alla volta".

## Comando eseguito

```
uv run python scripts/async_vs_sync.py
```

## Scenario A — micro-query (`SELECT 1`, 100 query)

| Modalità                          | Tempo osservato |
|-----------------------------------|-----------------|
| Sequenziale (`for` + await)       | {seq_a:.4f} s  |
| Parallelo (`asyncio.gather`)      | {par_a:.4f} s  |
| Speedup                           | {speedup_a:.1f}x |

Su query banali e in locale il guadagno è minimo (a volte negativo): il costo è dominato
dalla creazione contestuale di molte connessioni e dall'overhead di scheduling, non dall'attesa I/O.

## Scenario B — query con attesa reale (`SELECT pg_sleep(0.05)`, 50 query)

| Modalità                          | Tempo osservato |
|-----------------------------------|-----------------|
| Sequenziale (`for` + await)       | {seq_b:.4f} s  |
| Parallelo (`asyncio.gather`)      | {par_b:.4f} s  |
| Speedup                           | {speedup_b:.1f}x |

## Conclusione

Il parallelismo paga quando ogni chiamata **attende davvero** (I/O lento): con 50 query
da 50 ms lato server l'async le sovrappone e chiude in ~{(par_b):.3f}s invece di
~{(seq_b):.3f}s ({speedup_b:.0f}x). Con micro-query locali il guadagno sparisce perchè
non c'è attesa da nascondere. È il modello giusto per LLM/API esterne (G4), dove ogni
chiamata impiega centinaia di ms e un server sincrono bloccherebbe un thread per chiamata.
"""
    DOCS.write_text(content, encoding="utf-8")
    print(content)


if __name__ == "__main__":
    asyncio.run(main())

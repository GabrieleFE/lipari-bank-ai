"""Misura il costo reale del pipeline LLM: 10 conversazioni x 5 turni.

Dry-run con il FakeLLMProvider (chiavi API del .env sono placeholder): dimostra che la
pipeline persiste tokens/cost e che le query del cost report funzionano. Con chiavi reali
basta rimuovere l'override qui sotto e lanciare il server / gli endpoint normalmente.
"""

import asyncio
from datetime import UTC, datetime
from statistics import mean

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import settings
from src.llm.factory import get_llm_provider
from src.main import app
from tests.fakes import FakeLLMProvider

CONVERSATIONS = 10
TURNS = 5


async def run_experiment() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    provider = FakeLLMProvider(response_text="Risposta di prova")
    app.dependency_overrides[get_llm_provider] = lambda: provider

    start = datetime.now(UTC)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            for conv in range(1, CONVERSATIONS + 1):
                session_id = "new"
                for turn in range(1, TURNS + 1):
                    message = f"Conversazione {conv} turno {turn}: quanto costa un bonifico?"
                    resp = await client.post(
                        "/api/ai/chat",
                        json={"session_id": session_id, "message": message},
                    )
                    if resp.status_code != 200:
                        raise RuntimeError(
                            f"HTTP {resp.status_code}: {resp.text} (conv={conv} turn={turn})"
                        )
                    session_id = resp.json()["session_id"]

        async with engine.begin() as conn:
            totals = (
                await conn.execute(
                    text(
                        """
                        SELECT COALESCE(SUM(tokens), 0) AS tokens,
                               COALESCE(SUM(cost_eur), 0) AS cost,
                               COUNT(*) AS n
                        FROM chat_messages
                        WHERE created_at > :start
                        """
                    ),
                    {"start": start},
                )
            ).one()

            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT session_id,
                               COUNT(*) AS n,
                               SUM(tokens) AS tokens,
                               SUM(cost_eur) AS cost
                        FROM chat_messages
                        WHERE created_at > :start
                        GROUP BY session_id
                        """
                    ),
                    {"start": start},
                )
            ).fetchall()
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)
        await engine.dispose()

    per_conv = [r.cost for r in rows]
    print("=" * 62)
    print("ESPERIMENTO COSTO - 10 conversazioni x 5 turni (dry-run, fake provider)")
    print(f"Finestra di misura: {start.isoformat()}")
    print("=" * 62)
    print(f"Messaggi persistiti: {totals.n}")
    print(f"Token totali:        {totals.tokens}")
    print(f"Costo totale (eur):  {totals.cost:.6f}")
    if totals.n:
        print(f"Costo medio/messaggio: {totals.cost / totals.n:.6f} eur")
    if per_conv:
        print(f"Costo medio/conversazione: {mean(per_conv):.6f} eur")
    print("=" * 62)
    print("Con gpt-4o-mini reale (stima analitica): ~0.00018 eur/turno")
    print("=> ~0.0009 eur/conversazione => 10 conversazioni ~0.009 eur.")


if __name__ == "__main__":
    asyncio.run(run_experiment())
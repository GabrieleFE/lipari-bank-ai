"""Misura Recall@3 del retrieval RAG su 10 domande ground-truth.

Prerequisiti: server e dati ingeriti (uv run python scripts/ingest_docs.py),
OPENAI_API_KEY valida nel .env. Eseguire da configurato per il DB.
"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.llm.embedding_client import EmbeddingClient
from src.services.retrieval_service import RetrievalService

GROUND_TRUTH: list[tuple[str, str]] = [
    ("Quanto costa un bonifico SEPA istantaneo?", "commissioni_bonifico"),
    ("Qual è il costo del bonifico estero extra-SEPA?", "commissioni_bonifico"),
    ("A quanto ammonta il canone di gestione mensile del conto standard?", "regolamento_conti"),
    ("Quali sono le condizioni per lo scoperto non autorizzato sul conto?", "regolamento_conti"),
    ("Quanto costa la carta di credito Gold il secondo anno?", "condizioni_carta_credito"),
    ("Qual è il periodo senza interessi della carta di credito?", "condizioni_carta_credito"),
    ("Come recupero la password dimenticata dell'app?", "faq_supporto"),
    ("Come blocco la carta in caso di smarrimento?", "faq_supporto"),
    ("Il bonifico SEPA online è gratuito per i clienti under 30?", "faq_supporto"),
    ("Qual è il costo massimo dello scoperto concesso sul conto?", "regolamento_conti"),
]


async def main() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    embedding_client = EmbeddingClient()
    correct = 0
    async with session_factory() as session:
        retrieval = RetrievalService(session, embedding_client)
        for i, (question, expected_doc) in enumerate(GROUND_TRUTH, 1):
            results = await retrieval.retrieve(question, top_k=3)
            found = any(r.document_id == expected_doc for r in results)
            mark = "OK" if found else "KO"
            correct += int(found)
            best = max((r.similarity for r in results), default=0.0)
            docs = ", ".join(r.document_id for r in results)
            print(f"{i:2d}. {mark} sim={best:.3f} docs=[{docs}]  {question}")
        print(f"\nRecall@3: {correct}/{len(GROUND_TRUTH)} ({correct / len(GROUND_TRUTH):.0%})")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

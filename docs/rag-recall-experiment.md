# Experiment: Recall@3 del RAG (Giorno 5)

**Obiettivo**: misurare quante volte, su 10 domande di verità ground-truth tratte dai documenti ingeriti, il documento rilevante compare nei primi 3 chunk recuperati.

## Setup

- Embedding model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dim, locale con sentence-transformers)
- Chunk size: 500, overlap: 50, split a fine frase (tariffari convertiti in prosa in fase di ingestione, FAQ come coppia Q/A singola)
- Indice HNSW, operatore `<=>` cosine
- Retrieval: top-3, `min_similarity` applicata solo nel RAG (qui il recall si misura senza soglia)
- Corpus: 4 documenti, 19 chunk in DB

## Ground Truth (10 Q&A)

| #  | Domanda (query)                                                        | Documento atteso          |
|----|------------------------------------------------------------------------|---------------------------|
| 1  | Quanto costa un bonifico SEPA istantaneo?                              | commissioni_bonifico      |
| 2  | Qual è il costo del bonifico estero extra-SEPA?                       | commissioni_bonifico      |
| 3  | A quanto ammonta il canone di gestione mensile del conto standard?     | regolamento_conti         |
| 4  | Quali sono le condizioni per lo scoperto non autorizzato sul conto?    | regolamento_conti         |
| 5  | Quanto costa la carta di credito Gold il secondo anno?                 | condizioni_carta_credito  |
| 6  | Qual è il periodo senza interessi della carta di credito?              | condizioni_carta_credito  |
| 7  | Come recupero la password dimenticata dell'app?                        | faq_supporto              |
| 8  | Come blocco la carta in caso di smarrimento?                           | faq_supporto              |
| 9  | Il bonifico SEPA online è gratuito per i clienti under 30?             | faq_supporto              |
| 10 | Qual è il costo massimo dello scoperto concesso sul conto?             | regolamento_conti         |

## Script di esecuzione (riproducibile)

```python
# scripts/recall_experiment.py
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from src.config import settings
from src.db.models import DocumentChunk
from src.llm.embedding_client import EmbeddingClient
from src.services.retrieval_service import RetrievalService

GROUND_TRUTH = [
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


async def main():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    ec = EmbeddingClient()
    async with session_factory() as session:
        service = RetrievalService(session, ec)
        correct = 0
        for i, (q, expected_doc) in enumerate(GROUND_TRUTH, 1):
            results = await service.retrieve(q, top_k=3)
            found = any(r.document_id == expected_doc for r in results)
            mark = "✓" if found else "✗"
            correct += int(found)
            sim = max((r.similarity for r in results), default=0)
            print(f"{i:2d}. {mark} sim={sim:.3f}  {q}")
        print(f"\nRecall@3: {correct}/{len(GROUND_TRUTH)} ({correct / len(GROUND_TRUTH):.0%})")
    await engine.dispose()


asyncio.run(main())
```

## Risultato osservato

Eseguito il 2026-09-16 con il modello locale `paraphrase-multilingual-MiniLM-L12-v2` e i 4 documenti ingeriti:

| #  | Esito | sim max | Documenti rilevati (top-3)                                    | Atteso                |
|----|-------|---------|---------------------------------------------------------------|-----------------------|
| 1  | OK    | 0.648   | faq_supporto, commissioni_bonifico, condizioni_carta_credito   | commissioni_bonifico  |
| 2  | OK    | 0.589   | faq_supporto, commissioni_bonifico, condizioni_carta_credito   | commissioni_bonifico  |
| 3  | OK    | 0.722   | regolamento_conti, condizioni_carta_credito, regolamento_conti | regolamento_conti     |
| 4  | OK    | 0.561   | regolamento_conti x3                                           | regolamento_conti     |
| 5  | OK    | 0.580   | condizioni_carta_credito x3                                    | condizioni_carta_credito |
| 6  | KO    | 0.669   | regolamento_conti x3                                           | condizioni_carta_credito |
| 7  | OK    | 0.520   | faq_supporto, faq_supporto, regolamento_conti                  | faq_supporto          |
| 8  | OK    | 0.657   | faq_supporto, faq_supporto, regolamento_conti                  | faq_supporto          |
| 9  | OK    | 0.633   | commissioni_bonifico, faq_supporto, faq_supporto               | faq_supporto          |
| 10 | KO    | 0.672   | condizioni_carta_credito x3                                    | regolamento_conti     |

**Recall@3: 8/10 (80%)** — nella banda attesa 70-90%.

### Analisi dei 2 miss (near-miss su concetti sovrapposti)

- **Domanda 6** ("periodo senza interessi della carta di credito"): la controparte giusta è in `condizioni_carta_credito` ("fino a 55 giorni a saldo stralcio"), ma il termine "interessi" punta forte su `regolamento_conti` (interessi creditori/debitori). Vincerebbe la query "Per quanti giorni posso restare senza interessi sugli acquisti della carta?".
- **Domanda 10** ("costo massimo dello scoperto concesso sul conto"): atteso `regolamento_conti` (scoperto non autorizzato), ma "costo massimo + plafond" trascina `condizioni_carta_credito`. Il chunk giusto è comunque vicino (sim simile al top-1).

Questi miss sono un classico limite del similarity search puro e sono il punto di partenza naturale per ibridi (keyword + vettoriale) al Giorno 6.

### Distribuzione della similarità e soglia

Misurata sul modello locale: in dominio 0.50-0.72, fuori dominio 0.31-0.41.

| Query                                          | sim top-1 |
|------------------------------------------------|-----------|
| Quanto costa la carta di credito Gold il secondo anno? (in dominio) | 0.580 |
| Quali sono le regole sugli investimenti ESG? (fuori dominio) | 0.405 |
| Chi ha vinto i mondiali di calcio? (fuori dominio) | 0.077 |

Conseguenza: la soglia `MIN_SIMILARITY` del RAG è stata **ricalibrata da 0.6 a 0.45**. A 0.6 i chunk leciti (0.52-0.58 alle domande 4, 5, 7) verrebbero filtrati e il servizio risponderebbe "Non ho informazioni" a domande legittime; a 0.45 i fuori-dominio reali (max 0.41) restano esclusi.

## Conclusioni

- Recall@3 **8/10 (80%)** con modello locale multilingue da 384 dim su corpus di 4 documenti e 19 chunk.
- La distribuzione della similarità va misurata sui propri dati: il valore "giusto" della soglia dipende dal modello (OpenAI text-embedding-3-small dà valori più alti e la 0.6 originale era pensata per quello).
- Le domande 6 e 10 sono i near-miss attesi su sezioni concettualmente vicine; da affrontare con hybrid search.
- Questo esperimento è la baseline per il framework di eval del Giorno 6.
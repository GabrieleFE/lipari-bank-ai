# Recap Settimana 1 — LipariBank AI (Giorni 1-6)

Bootcamp Python AI Powered v1 — Lipari Consulting. Progetto: LipariBank AI Assistant.

---

## Giorno 6 — Eval is the new test

La giornata ha costruito il framework di eval: il passaggio dalla mentalita' binaria
(assert ==) a quella distributiva (su 100 casi ne prende 94).

**Cosa è stato fatto:**

1. **Golden dataset** (`src/eval/datasets/`), versionati in Git come codice:
   - `categorize_golden.jsonl` — 32 esempi annotati a mano, mix happy path + edge case
     (descrizioni troncate, scritte male, ambigue, uppercase, PAYPAL\*, stipendio).
   - `rag_golden.jsonl` — 25 domande con `expected_doc_id` sui 4 documenti reali
     (baseline: l'esperimento Recall@3 del Giorno 5).
   - `generation_golden.jsonl` — 6 domande per l'eval end-to-end del RAG.

2. **Eval runners** (`src/eval/runners.py`), struttura unica per tutti i task:
   *carica -> cicla -> confronta -> aggrega -> decide*.
   - `eval_categorize` — accuracy + failures (input/atteso/ottenuto) + latenza.
   - `eval_rag` — recall@k + recall@1 (senza chiamare alcun LLM: quasi gratis).
   - `llm_judge` — LLM-as-judge con 4 criteri espliciti (faithfulness, relevance,
     completeness, citation) e output JSON; `eval_generation` end-to-end RAG.
   - `EvalResult` + `compare_models` — la tabella costo/qualità/latenza per decidere.

3. **Gate CI**: il runner CLI (`uv run python -m src.eval.runners`) fa `exit(1)` sotto soglia.

4. **pytest + eval**: marker `eval` in `pyproject.toml`, test in `tests/evals/`.
   - `uv run pytest` skippa gli eval (nessun costo a ogni commit);
   - `uv run pytest -m eval` li lancia (CI settimanale: `.github/workflows/eval.yml`).

5. **Config/abstraction**: `judge_model` in Settings, `get_llm_provider(model=...)`
   e `get_categorize_service(model=...)` per confronti A/B a una variabile.

6. **AI Code Review L3** su RAGService: una riga `CRITICAL` prompt-injection portata a
   G7, cost-guardrail mancante sull'advice, `top_k` hardcoded, citazioni da chunk
   recuperati e eval mancante (aggiunto oggi).

**I principi chiave** (le slide da portare a colloquio):

- Un test che cade 1 volta su 100 e' peggio di uno che cade sempre: la squadra impara a
  rilanciarlo. L'eval non sostituisce gli unit test: il chunking e il prompt si testano
  con `assert ==`; l'eval copre solo il pezzo probabilistico.
- Il valore sta nei `failures`, non nell'accuracy: il numero dice che c'e' un problema,
  i casi falliti dicono quale.
- Judge != generatore (self-bias), criteri espliciti e separati, dataset congelato e
  una variabile alla volta, soglia = livello sotto cui il servizio non e' accettabile.
- Gli eval costano: PR = smoke test (1-5 esempi), cadenza fissa = dataset intero.

---

## Giorni precedenti (1-5)

### Giorno 1 — Async in Python
Fondamenta: `asyncio`, event loop, `to_thread` per il CPU-bound, niente sync in async.
Esperimento documentato in `docs/async-vs-sync-experiment.md`. Il file
`src/llm/embedding_client.py` porta ancora oggi quella scelta: l'encode sentence-
transformers gira su un thread per non bloccare FastAPI.

### Giorno 2 — FastAPI + Pydantic v2
Endpoint binari e contracts: `src/api/` (categorize, chat, advice), `src/types/`
modelli Pydantic v2 con validazione (Currency pattern, amount > 0, confidence bounds).
Dipendenza auth sul router dichiarata come debito (JWT in config ma non applicato).

### Giorno 3 — DB async (SQLAlchemy 2.0 + pgvector)
Sessioni async, `alembic` per le migration, modelli `ChatSession`/`ChatMessage`/
`DocumentChunk`. Cost tracking da subito non banale: `tokens`, `cost_eur`,
`model_used`, `created_at` persistiti su ogni messaggio. E' l'investimento che oggi
rende possibile il cost report: senza quei campi, sarebbe una migration e una
settimana di dati mancanti.

### Giorno 4 — Abstraction layer LLM
`src/llm/`: `LLMProvider` come Protocol strutturale (OpenAI e Anthropic uguali a valle),
`factory` come unico punto di scelta del modello, prompt versionati come file
(`src/prompts/*.md`), `max_tokens` e `CostTracker` (soglia giornaliera). Il numero di
versione nel nome del prompt diventa oggi (G6) la condizione per un A/B onesto.

### Giorno 5 — RAG pipeline
Ingest (chunking a fine frase, 500/50), embedding locale self-hosted
`paraphrase-multilingual-MiniLM-L12-v2` (i dati non escono dalla banca), pgvector
HNSW, `RetrievalService` (SQL raw con parametri legati), `RAGService` con grounding
"ESCLUSIVAMENTE dai contesti" e citation. Esperimento Recall@3: 8/10 (80%), soglia
`MIN_SIMILARITY` misurata e ricalibrata a 0.45.

---

## Architettura a fine settimana

```
FastAPI (src/api) ---- CategorizeService ----------> Instructor/AsyncOpenAI
        |             ChatService -----------------> LLMProvider (OpenAI/Anthropic)
        |             RAGService -------> RetrievalService -------> pgvector (384d)
        |                    |-> LLMProvider (grounding + citation)
        +------ CostTracker (soglia giornaliera) + ChatMessage (tokens/cost/model)

src/eval (G6)        golden datasets (JSONL versionati) -> runners -> gate
tests/evals          pytest -m eval  (CI settimanale), unit test a ogni commit
```

**Prossimo (G7)**: async al posto giusto (gather + semaforo nel runner, cache embedding,
cost-guardrail su advice, citazioni credibili dai `[doc_id]`, prompt-injection sanitize).
# AI Code Review L3 — RAGService

**Data**: 2026-09-17
**File**: `src/services/rag_service.py` (81 LOC) + `src/services/retrieval_service.py`
**Reviewer**: Claude 4 (AI Code Review L3 — senior prompt master)

## Issue

| # | Severity | Tipo | Linea | Issue | Fix | Decisione |
|---|----------|------|-------|-------|-----|-----------|
| 1 | CRITICAL | prompt-injection | 47 | `req.question` (input dell'utente) concatenato nel prompt senza separazione netta dati/istruzioni. Un contenuto che contiene istruzioni puo' esfiltrare il system prompt o aggirare i guardrail. Non esiste l'equivalente del parametro legato: l'unica difesa e' racchiudere l'input in marcatori espliciti | Delimita CONTESTI e DOMANDA con marcatori markdown e istruisci il modello a trattarli come dati; tronca la question a lunghezza massima | ⏳ G7 (sanitize + separatori) |
| 2 | MAJOR | cost-guardrail | 51 | `llm.complete` non passa dal `CostTracker`: il budget giornaliero (G4) vale per la chat ma NON per il percorso advice. Un RAG generoso moltiplica chiamate a costo pieno senza alcun freno | Iniettare `cost_tracker` nel router advice e chiamare `check_budget()` prima di completare | ✅ Fix (G7) |
| 3 | MAJOR | config | 29 | `top_k=5` hardcoded nella chiamata; `MIN_SIMILARITY` come attributo di classe (misurato il 16/09, ma e' un dato che dipende dal modello di embedding) | Portare entrambi in `Settings` (`retrieval_top_k`, `retrieval_min_similarity`) | ✅ Fix |
| 4 | MAJOR | grounding | 66-74 | Le citations sono i chunk RECUPERATI, non quelli che il modello ha davvero usato (debito dichiarato in docstring). Il vincolo "ESCLUSIVAMENTE dai contesti" del system prompt resta un auspicio finche' non e' misurabile | Estrarre `[doc_id: X]` dal testo generato per le citazioni credibili + eval faithfulness (G6) | ⏳ G7 + eval G6 (fatto) |
| 5 | MAJOR | eval | — | Al giorno 5 non esisteva alcun modo di misurare recall o faithfulness: i retochi al prompt erano «mi sembra migliore» | Aggiunto `src/eval/datasets/rag_golden.jsonl` (25 casi), `generation_golden.jsonl`, runner recall@k e LLM-as-judge | ✅ Done (G6) |
| 6 | MINOR | perf | 47-49 | La domanda e' ri-embedded a ogni chiamata senza cache; query identiche ripetute pagano l'embedding piu' volte | Hash SHA-256 della query normalizzata -> cache TTL 1h (in-memory o Redis) | ⏳ G7 |
| 7 | INFO | robustness | 33-40 | Il rifiuto "Non ho informazioni" restituisce cost 0 e cross ingres: va bene, ma non distingue un fuori-dominio da un retrieval rotto (DB vuoto) | Includere un flag `grounded: bool` nella risposta per l'observability | ⏸ backlog |

## What's done well

1. Pipeline RAG pulita e lineare: retrieve → context build → generate → citation.
2. La soglia `MIN_SIMILARITY` e' stata MISURATA sui propri dati (esperimento 16/09, in dominio 0.50-0.72 / fuori dominio 0.31-0.41) e ricalibrata da 0.6 a 0.45 — non indovinata.
3. Il rifiuto esplicito quando non ci sono chunk evita l'allucinazione di default.
4. Cost e token propagati nella response (`tokens_used`, `cost_eur`): la contabilita' del G3 arriva anche al RAG.
5. SQL raw nel retrieval con parametri sempre legati (`:query_emb`, `:top_k`), mai interpolati.

---

### Note di metodo

- Il commit del 17/09 ha aggiunto il framework di eval (G6) attivo: `rag_golden.jsonl`, `categorize_golden.jsonl`, `generation_golden.jsonl`, runner recall@k/MRR e LLM-as-judge. La prossima review verifica che ogni nuova chiamata LLM abbia un golden case associato: senza, la PR e' incompleta per definizione.
- L'endpoint `/api/ai/advice` NON e' sotto `JWTSecret`/admin (debito G2 dichiarato): il cost report aggregato e il RAG restano pubblici finche' non si aggiunge la dipendenza di autorizzazione sul router.
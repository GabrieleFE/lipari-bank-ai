# LipariBank AI Assistant

Backend AI-powered per LipariBank. Built during Python Bootcamp AI Powered v1 — Lipari Consulting.

## Tech Stack

- Python 3.12+
- FastAPI (async, type-driven)
- Pydantic v2 (validation + serialization)
- SQLAlchemy 2.0 (async, typed `Mapped`/`mapped_column`) + asyncpg
- PostgreSQL 16 + pgvector (Docker)
- Alembic (migration versionate)
- uv (package manager)
- mypy strict (type checking)
- ruff (linting + formatting)
- pytest + pytest-asyncio
- OpenAI + Anthropic SDK (async), Instructor (structured output), tenacity (retry/backoff)

## Quickstart

```bash
# Install uv (if not installed)
curl -LsSf https://astral.sh/uv/install.sh | sh  # Linux/Mac
# or
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"  # Windows

# Install deps
uv sync

# Copy env vars
cp .env.example .env
# Edit .env with your keys

# Avvia PostgreSQL+pgvector (porta 5433, la 5432 è riservata ad un Postgres locale)
docker compose up -d

# Applica le migration (tabelle chat_sessions / chat_messages)
uv run alembic upgrade head

# Start dev server
uv run uvicorn src.main:app --reload
```

Server runs at http://127.0.0.1:8000

- `/health` — health check endpoint
- `/api/ai/chat` — multi-turn chat persistente su PostgreSQL (`"new"` crea una sessione, un UUID la prosegue; LLM reale con provider injection)
- `/api/ai/chat/stream` — streaming SSE chunk-by-chunk della risposta (stessa persistenza)
- `/api/ai/categorize` — categorizzazione transazioni via LLM + Instructor (structured output Pydantic)
- `/docs` — Swagger UI

## Design decisions

### Giorno 4 — Abstraction layer con entrambi i provider (OpenAI + Anthropic)

Scelgo l'astrazione con entrambi i provider per tre ragioni: (1) per una banca la resilienza a un provider down pesa più della velocità di consegna; (2) il costo dell'interfaccia è già pagato in ~60 righe (`src/llm/client.py`), e la factory riduce un cambio fornitore a una riga in `.env`; (3) i test del Giorno 7 sfruttano la stessa injectabilità. Il motto è: la dipendenza da un fornitore non si evita scegliendone uno migliore, si evita con un'interfaccia propria in mezzo.

### Sliding door A — response_model esplicito vs return type hint

Sugli endpoint uso **`response_model` esplicito** insieme al return type hint. Il type hint lo legge mypy e mi protegge mentre scrivo; `response_model` lo esegue FastAPI a runtime e **valida e filtra** l'output reale (es. impedisce che un refactoring interno faccia trapelare campi nel contratto pubblico). Su endpoint pubblici servono tutti e due: sceglierne uno solo significa rinunciare a metà della protezione.

### Giorno 3 — Alembic vs `create_all()`

Scelgo **Alembic**: le migration diventano versionate e riproducibili su qualsiasi ambiente (dev, prod), si possono applicare/rollback con `upgrade`/`downgrade` e autogenerate cattura i cambi di schema senza toccare i dati. `create_all()` è comodo in demo ma è muto: non traccia le evoluzioni dello schema, non fa downgrade e su un DB esistente è pericoloso. Non appena le tabelle ospitano dati veri (qui i messaggi della chat), serve la migration versionata.

## Error handling

Gestione centralizzata degli errori in `src/exceptions.py` + handler in `src/main.py`:

- `AppError` → status code custom + body `ErrorResponse` uniforme
- `RequestValidationError` → 422 con `details` (lista di `campo: messaggio`)
- `Exception` generico → 500 con messaggio generico (niente stack trace esposto)

## Middleware

- **Request-id**: header `X-Request-Id` (UUID per richiesta) + `X-Process-Time`
- **CORS**: origins consentiti (`localhost:4200`, `localhost:5173`)

## Persistenza (Giorno 3)

- `src/db/session.py` — engine async + `async_sessionmaker` + `get_db` (sessione per-request via `Depends`).
- `src/db/models.py` — `ChatSession` (1→N) `ChatMessage`: `Mapped`/`mapped_column`, UUID, `relationship` con `cascade="all, delete-orphan"` e `back_populates`.
- `src/db/repos.py` — `ChatRepository`: isola l'accesso al DB; `find_session` usa `selectinload` per evitare N+1.
- `src/services/chat_service.py` — orchestrazione: risolve la sessione, persiste i messaggi user/assistant.
- Sessione inesistente → `404 CHAT_SESSION_NOT_FOUND`.

### Starter del collega (difetti individuati/corretti)

`starter-collega` non è presente nel repo; per indicazione del progetto la funzionalità è
stata implementata da zero su `src/llm/`, `src/services/` e `src/prompts/`. In fase di
implementazione sono stati individuati e corretti questi difetti:

1. **Tipo `Mapped[str]` su colonna UUID** — dichiarare `id: Mapped[str]` su una colonna `UUID(as_uuid=True)` mente sul tipo runtime (SQLAlchemy torna un oggetto `UUID`, non `str`) → pydantic rifiutava la risposta con `Input should be a valid string`. Corretto tipizzando il layer DB con `uuid.UUID` e convertendo a `str` solo nel confine API dove `ChatResponse` lo richiede.
2. **Pool condiviso tra event loop nei test** — con `asyncio_mode=auto` ogni test ha un loop nuovo ma le connessioni asyncpg del pool restavano legate al loop precedente → `RuntimeError: Event loop is closed`. Corretto con un engine dedicato `NullPool` per-test via `dependency_overrides` su `get_db`.
3. **Risposta del provider costruita dentro il service** — costruire il client LLM nell'endpoint significa aprire/chiudere il pool HTTP e il TLS a ogni richiesta, e rende i test dipendenti dalla rete. Corretto con una factory singleton (`get_llm_provider`) iniettata con `Depends`, sostituibile nei test.
4. **429 senza `retry_after`** — `RateLimitError` portava l'attributo ma il global handler non lo esponeva: il client non sapeva quando riprovare. Corretto propagando `retry_after` nel body e nell'header `Retry-After` (vedi `src/main.py`).
5. **Temperature ignorata / non controllata** — nessuna scelta esplicita per compito. Corretto: categorizzazione ~temperatura bassa (structured output via Instructor), chat a 0.3 nei provider; `temperature` vive dentro il provider, non nella firma `complete` (evita di far trapelare dettagli fornitore nel contratto).

## Integrazione LLM (Giorno 4)

- `src/llm/types.py` — dati del dominio LLM: `Message` (ruoli Literal, incl. `"tool"`, come da procedura), `LLMResponse` (testo + token + costo + modello), `StreamChunk` (ultimo chunk con contabilità). Re-esportati da `client.py`, che resta l'unico modulo che il codice applicativo importa.
- `src/llm/client.py` — `LLMProvider` (Protocol strutturale) + re-export dei tipi. Non esistono classi base da ereditare.
- `src/llm/openai_provider.py` / `anthropic_provider.py` — le tre divergenze (system prompt, lettura risposta, conteggio token) vivono **solo qui**. Costo calcolato separando input/output (tariffe diverse); `PRICING` come dato, non costante.
- `src/llm/retry.py` — backoff esponenziale via tenacity (1s,2s,4s,8s). Gli SDK sono costruiti con `max_retries=0` per evitare retry doppi (SDK + wrapper).
- `src/llm/factory.py` — `get_llm_provider`: singleton a partire da `DEFAULT_MODEL` (`gpt*` → OpenAI, `claude*` → Anthropic), da usare sempre con `Depends`.
- `src/prompts/` — prompt versionati come file (`chat_system_v1.md`, `categorize_system_v1.md`): review-able, diff-able, confrontabili dal Giorno 6. Caricati da `src/llm/prompts.py`.
- `src/services/categorize_service.py` — `CategorizeService` con Instructor: schema Pydantic imposto al modello, `max_retries=2` per i retry su validazione e `temperature=0.0` per un output deterministico (come da procedura).
- `src/observability/cost_tracker.py` — `CostTracker`: somma di `chat_messages.cost_eur` della giornata; sopra soglia → `RateLimitError` (→ 429 con `Retry-After`). `date | None = None` nella firma evita il default valutato all'import (B008).
- Streaming: `/api/ai/chat/stream` usa SSE con `data: {"delta": "..."}` per evitare rotture da a-capo, termina con `data: [DONE]`. La contabilità streaming arriva nell'ultimo chunk (`include_usage` su OpenAI, `get_final_message().usage` su Anthropic).
- Cost tracking: ogni `ChatMessage` assistant salva `tokens`, `cost_eur`, `model_used`. Vedi `docs/llm-cost-experiment.md` per i numeri misurati e le query SQL.

## Development

```bash
# Type check
uv run mypy src/

# Lint
uv run ruff check src/ tests/ alembic/env.py

# Format
uv run ruff format src/ tests/ alembic/env.py

# Test
uv run pytest

# Migration
uv run alembic revision --autogenerate -m "desc"
uv run alembic upgrade head
uv run alembic downgrade -1
```

## Project Structure

```
src/
├── api/
│   ├── chat.py        # POST /api/ai/chat, POST /api/ai/chat/stream (SSE)
│   └── categorize.py  # POST /api/ai/categorize (Instructor)
├── llm/
│   ├── client.py          # Protocol LLMProvider (+ re-export tipi da types.py)
│   ├── types.py           # Message, LLMResponse, StreamChunk
│   ├── openai_provider.py # OpenAIProvider (cost tracking, stream con usage)
│   ├── anthropic_provider.py # AnthropicProvider (system separato, stream con usage)
│   ├── retry.py           # tenacity: backoff esponenziale sugli errori transitori
│   ├── prompts.py         # caricamento dei prompt versionati da src/prompts/
│   └── factory.py         # get_llm_provider (singleton, Depends)
├── prompts/
│   ├── chat_system_v1.md      # system prompt chat (versionato)
│   └── categorize_system_v1.md # system prompt categorizzazione (versionato)
├── observability/
│   └── cost_tracker.py    # CostTracker: budget giornaliero -> 429
├── db/
│   ├── models.py      # SQLAlchemy 2.0: ChatSession, ChatMessage (tokens, cost_eur)
│   ├── repos.py       # ChatRepository (selectinload, no N+1)
│   ├── session.py     # engine async + get_db per-request
│   └── __init__.py
├── services/
│   ├── chat_service.py      # business logic chat + history multi-turn + persistenza
│   └── categorize_service.py # structured output via Instructor
├── types/
│   ├── chat.py        # ChatRequest, ChatResponse, ToolCallInfo
│   ├── categorize.py  # CategorizeRequest, CategorizeResponse, CategoryEnum
│   ├── advice.py      # AdviceRequest, AdviceResponse, Citation (anticipato)
│   ├── ingest.py      # DocumentIngestRequest (anticipato)
│   └── error.py       # ErrorResponse globale
├── config.py          # Pydantic Settings (env vars, incl. max_daily_cost_eur)
├── exceptions.py      # AppError + sottoclassi (RateLimitError con retry_after)
├── middleware.py      # CORS + request-id
└── main.py            # FastAPI app + handler + router
scripts/
└── cost_experiment.py     # dry-run del costo: 10 conversazioni x 5 turni + report SQL
alembic/
    env.py             # configurato per engine async
    versions/          # migration versionate
    script.py.mako
docker-compose.yml     # PostgreSQL 16 + pgvector (porta 5433)
docs/
└── llm-cost-experiment.md  # misura del costo reale + query SQL
tests/
├── conftest.py
├── fakes.py           # FakeLLMProvider / FakeCategorizeService (niente rete nei test)
├── test_health.py
├── test_chat.py       # incl. budget 429 e streaming SSE
├── test_categorize.py
└── test_types.py
```
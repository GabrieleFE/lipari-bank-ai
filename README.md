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
- `/api/ai/chat` — multi-turn chat persistente su PostgreSQL (`"new"` crea una sessione, un UUID la prosegue; LLM reale in G4)
- `/api/ai/categorize` — categorizzazione transazioni via keyword (dummy)
- `/docs` — Swagger UI

## Design decisions

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

`starter-collega` non è presente nel repo; la funzionalità è stata implementata da zero. I difetti individuati e corretti in fase di implementazione:

1. **Tipo `Mapped[str]` su colonna UUID** — dichiarare `id: Mapped[str]` su una colonna `UUID(as_uuid=True)` mente sul tipo runtime (SQLAlchemy torna un oggetto `UUID`, non `str`) → pydantic rifiutava la risposta con `Input should be a valid string`. Corretto tipizzando il layer DB con `uuid.UUID` e convertendo a `str` solo nel confine API dove `ChatResponse` lo richiede.
2. **Pool condiviso tra event loop nei test** — con `asyncio_mode=auto` ogni test ha un loop nuovo ma le connessioni asyncpg del pool restavano legate al loop precedente → `RuntimeError: Event loop is closed`. Corretto con un engine dedicato `NullPool` per-test via `dependency_overrides` su `get_db`.

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
│   ├── chat.py        # POST /api/ai/chat (multi-turn, persistente)
│   └── categorize.py  # POST /api/ai/categorize (dummy keyword)
├── db/
│   ├── models.py      # SQLAlchemy 2.0: ChatSession, ChatMessage
│   ├── repos.py       # ChatRepository (selectinload, no N+1)
│   ├── session.py     # engine async + get_db per-request
│   └── __init__.py
├── services/
│   └── chat_service.py # business logic chat + persistenza
├── types/
│   ├── chat.py        # ChatRequest, ChatResponse, ToolCallInfo
│   ├── categorize.py  # CategorizeRequest, CategorizeResponse, CategoryEnum
│   ├── advice.py      # AdviceRequest, AdviceResponse, Citation (anticipato)
│   ├── ingest.py      # DocumentIngestRequest (anticipato)
│   └── error.py       # ErrorResponse globale
├── config.py          # Pydantic Settings (env vars)
├── exceptions.py      # AppError + sottoclassi
├── middleware.py      # CORS + request-id
└── main.py            # FastAPI app + handler + router
alembic/
    env.py             # configurato per engine async
    versions/          # migration versionate
    script.py.mako
docker-compose.yml     # PostgreSQL 16 + pgvector (porta 5433)
tests/
├── conftest.py
├── test_health.py
├── test_chat.py
├── test_categorize.py
└── test_types.py
```
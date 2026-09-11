# LipariBank AI Assistant

Backend AI-powered per LipariBank. Built during Python Bootcamp AI Powered v1 — Lipari Consulting.

## Tech Stack

- Python 3.12+
- FastAPI (async, type-driven)
- Pydantic v2 (validation + serialization)
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

# Start dev server
uv run uvicorn src.main:app --reload
```

Server runs at http://127.0.0.1:8000

- `/health` — health check endpoint
- `/api/ai/chat` — multi-turn chat (echo, LLM reale in G4)
- `/api/ai/categorize` — categorizzazione transazioni via keyword (dummy)
- `/docs` — Swagger UI

## Design decisions

### Sliding door A — response_model esplicito vs return type hint

Sugli endpoint uso **`response_model` esplicito** insieme al return type hint. Il type hint lo legge mypy e mi protegge mentre scrivo; `response_model` lo esegue FastAPI a runtime e **valida e filtra** l'output reale (es. impedisce che un refactoring interno faccia trapelare campi nel contratto pubblico). Su endpoint pubblici servono tutti e due: sceglierne uno solo significa rinunciare a metà della protezione.

## Error handling

Gestione centralizzata degli errori in `src/exceptions.py` + handler in `src/main.py`:

- `AppError` → status code custom + body `ErrorResponse` uniforme
- `RequestValidationError` → 422 con `details` (lista di `campo: messaggio`)
- `Exception` generico → 500 con messaggio generico (niente stack trace esposto)

## Middleware

- **Request-id**: header `X-Request-Id` (UUID per richiesta) + `X-Process-Time`
- **CORS**: origins consentiti (`localhost:4200`, `localhost:5173`)

## Development

```bash
# Type check
uv run mypy src/

# Lint
uv run ruff check src/

# Format
uv run ruff format src/

# Test
uv run pytest
```

## Project Structure

```
src/
├── api/
│   ├── chat.py        # POST /api/ai/chat (echo)
│   └── categorize.py  # POST /api/ai/categorize (dummy keyword)
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
tests/
├── test_health.py
├── test_chat.py
├── test_categorize.py
└── test_types.py
```
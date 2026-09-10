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
- `/docs` — Swagger UI

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
├── config.py      # Pydantic Settings (env vars)
├── main.py        # FastAPI app + /health
tests/
└── test_health.py
```

# Lipari Bank AI

Progetto del bootcamp **Python AI-Powered v3**: API FastAPI con RAG (pgvector), classificazione
automatica delle transazioni e assistente finanziario, più una console web statica.

## Quickstart

### Prerequisiti

- Python 3.12 (fissato in `.python-version`, letto automaticamente da `uv`)
- [uv](https://docs.astral.sh/uv/): gestisce Python, virtualenv e dipendenze
- Docker Desktop (PostgreSQL 16 con pgvector)

Non serve installare Python 3.12 a mano: `uv` lo scarica e lo usa per il virtualenv.

### 1. Configurazione dell'ambiente

Copia il file di configurazione:

```powershell
Copy-Item .env.example .env
```

```bash
cp .env.example .env
```

Apri `.env` e compila:

- `APP_NAME`, `ENVIRONMENT` (`local` in sviluppo), `DEBUG`
- `DATABASE_URL` (stesso valore di `docker-compose.yml`)
- `OPENAI_API_KEY` e/o `ANTHROPIC_API_KEY`: facoltative per l'avvio, obbligatorie per usare i provider
- modelli: `DEFAULT_MODEL`, `CATEGORIZE_MODEL`, `JUDGE_MODEL`, `EMBEDDING_MODEL`
- parametri: `EMBEDDING_DIM`, `MAX_TOKENS_PER_REQUEST`, `LLM_TIMEOUT_SECONDS`,
  `MAX_DAILY_COST_EUR`, `HISTORY_MAX_MESSAGES`
- `JWT_SECRET`: stringa casuale di almeno 32 caratteri

Per generare il segreto JWT con qualsiasi Python 3.12:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Se `.env` manca una variabile obbligatoria o un valore ha il tipo sbagliato, l'app **non parte** e
il messaggio dice quale campo correggere e cosa ci si aspetta (per esempio
`MAX_TOKENS_PER_REQUEST: deve essere un numero intero`). I valori dei segreti non finiscono mai nel
messaggio di errore.

### 2. Installazione delle dipendenze

```bash
uv sync --locked
```

`--locked` installa esattamente le versioni di `uv.lock`, che è tracciato in Git: un collega che
clona il repo ottiene lo stesso ambiente, senza sorprese.

#### Scelta: dependency group `dev`

I tool di sviluppo (pytest, mypy, ruff, alembic) stanno nel dependency group PEP 735 `dev`, non
come dipendenze "extra" del pacchetto: è lo standard più recente per `uv add --dev` e mantiene
`pyproject.toml` leggibile. `uv sync` installa già il gruppo `dev` per default, quindi l'ambiente è
completo subito. In produzione si usa `uv sync --no-dev --locked`.

### 3. Database

```bash
docker compose up -d db
uv run alembic upgrade head
```

### 4. Avvio

```bash
uv run uvicorn src.main:app --reload
```

- Documentazione interattiva: http://localhost:8000/docs
- Health check: http://localhost:8000/health (su Windows: `curl.exe http://localhost:8000/health`)

Esempio di risposta quando le chiavi dei modelli non sono configurate:

```json
{
  "status": "DEGRADED",
  "timestamp": "2026-09-25T10:34:54.066513Z",
  "app_name": "LipariBank AI",
  "version": "1.0.0",
  "environment": "local",
  "credentials": {
    "openai_configured": false,
    "anthropic_configured": false,
    "missing": ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]
  }
}
```

`/health` restituisce `200` con `status: UP` solo se le credenziali richieste sono presenti, e
`503` con `status: DEGRADED` se ne manca qualcuna. La risposta dice sempre in quale ambiente sta
girando l'app e quali credenziali mancano, ma **mai** i loro valori.

## Come funziona

- `/api/chat` (main + RAG): il messaggio viene embeddato, si cercano k chunk simili in pgvector e
  il contesto recuperato viene passato al modello con le fonti numerate `[1]`, `[2]`.
- `/api/advice`: genera un piano d'azione con struttura fissa e controlla che ogni affermazione sia
  supportata da una citazione (allineamento, astensione).
- `/api/categorize`: classifica la transazione in una delle 6 categorie previste e valuta la
  confidenza.
- Console web statica in `web/`, servita dalla stessa origin (nessun CORS necessario).

## Struttura del progetto

```
src/
  main.py              # app FastAPI, health, handler errori
  config.py            # settings tipizzate (Pydantic Settings, SecretStr, fail-fast)
  api/                 # router: chat, advice, categorize
  db/                  # modelli SQLAlchemy, sessione, vettori pgvector
  llm/                 # factory, client OpenAI/Anthropic, embedding client
  rag/                 # chunking e retrieval ibrido
  services/            # logica applicativa: chat, advice, categorize, ingest, retrieval
  schemas/             # modelli di richiesta/risposta
  prompts/             # prompt su file, versionati e ispezionabili
  middleware/          # request id, logging
tests/                 # test unitari e di contratto
scripts/               # benchmark di asyncio (sync vs async)
docs/ai-review/        # review G1
docs/recap-g1-v3.md    # appunti di studio G1
```

## Comandi

| Azione | Comando |
| --- | --- |
| Avvio | `uv run uvicorn src.main:app --reload` |
| Formattazione | `uv run ruff format .` |
| Controllo formato | `uv run ruff format --check .` |
| Lint | `uv run ruff check .` |
| Type check | `uv run mypy src tests scripts alembic/env.py` |
| Test (default, senza eval) | `uv run pytest` |
| Solo test eval (servono API key) | `uv run pytest -m eval` |
| Migrazioni | `uv run alembic upgrade head` |
| Database | `docker compose up -d db` |
| Ambiente pulito | `Remove-Item -Recurse -Force .venv` poi `uv sync --locked` |

`uv run pytest` esclude di default i test `eval` (costosi, chiamano le API reali): si eseguono solo
su richiesta esplicita con `uv run pytest -m eval`. `mypy` gira in modalità strict su `src`, `tests`,
`scripts` e `alembic/env.py`, senza eccezioni per i test.

## Configurazione

| Variabile | Default | Note |
| --- | --- | --- |
| `ENVIRONMENT` | `local` | `local`, `test`, `staging`, `production` |
| `DATABASE_URL` | - | obbligatoria, stringa di connessione con driver async |
| `OPENAI_API_KEY` | - | facoltativa all'avvio, richiesta da OpenAI |
| `ANTHROPIC_API_KEY` | - | facoltativa all'avvio, richiesta da Anthropic |
| `JWT_SECRET` | - | obbligatoria, almeno 32 caratteri |
| `MAX_TOKENS_PER_REQUEST` | `2000` | limite per singola richiesta LLM |
| `LLM_TIMEOUT_SECONDS` | `60.0` | timeout delle chiamate esterne |
| `MAX_DAILY_COST_EUR` | `5.0` | budget giornaliero |
| `HISTORY_MAX_MESSAGES` | `20` | messaggi di cronologia mantenuti |

L'elenco completo e commentato è in `.env.example` (una riga di commento per variabile). Il file
`.env` non è tracciato in Git e i segreti restano fuori dal repository.

## Sicurezza

- Nessun segreto nel codice: le chiavi sono `SecretStr`, lette solo da `.env` e richieste
  (`require_secret`) solo quando serve il provider.
- `/health` espone solo booleani e nomi delle variabili mancanti, mai valori o frammenti.
- I messaggi di errore di configurazione non includono l'input ricevuto, quindi non possono
  stampare un segreto per sbaglio.

## Ricostruzione dell'ambiente (verifica reale)

- Cosa ho fatto: cancellato il virtualenv e ricreato da zero con `uv venv --clear` seguito da
  `uv sync --locked`.
- Tempo misurato: **~17 secondi** (93 pacchetti, con cache `uv` locale già calda; dalla cache
  vuota il primo sync scarica anche torch e transformers e richiede più tempo).
- Primo ostacolo per un collega: non è tecnico, è creare `.env` con `JWT_SECRET` e le chiavi API
  (senza, l'app si ferma con il messaggio fail-fast) e avere Docker attivo per PostgreSQL.
- Nota Windows: se la console non mostra correttamente le lettere accentate nei messaggi,
  digita `chcp 65001` per passare la console a UTF-8.

## Tecnologie usate

- Python 3.12 (tipizzazione stretta, `Protocol`, `async`/`await`)
- FastAPI + Pydantic v2 + pydantic-settings
- SQLAlchemy 2 async + asyncpg + pgvector
- httpx, tenacity, structlog
- pytest + pytest-asyncio, ruff, mypy strict
- uv per ambiente e dipendenze riproducibili

## Roadmap

- **Milestone 1 - Fondazione (COMPLETATA)**: struttura, config, DB, Docker, middleware, health.
  Gate G1 v3 verificato: lockfile, tipi, segreti, health veritiero, ricostruzione da zero.
- **Milestone 2 - Dominio e AI (COMPLETATA)**: RAG ibrido, embedding, classificazione, advice.
- **Milestone 3 - Qualità (COMPLETATA)**: test suite, eval, CI, review.
- **Milestone 4 - Hardening**: auth JWT reale, rate limit, osservabilità, budget enforcement.
- **Milestone 5 - Produzione**: deploy, monitoraggio, documentazione operativa.

## Note

Progetto didattico. Le valutazioni quantitative (eval su dataset) sono indicative e vanno
rivalidate prima di qualsiasi uso in produzione.

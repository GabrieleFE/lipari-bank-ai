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
- `CORS_ORIGINS`: elenco di origini autorizzate separate da virgola, default `http://localhost:4200`
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

## La giornata 2 in un comando

```bash
uv run python scripts/day2_smoke.py
```

Lo script avvia l'app su una porta libera, esegue le verifiche via HTTP e stampa per ognuna cosa ha
chiesto, cosa si aspettava e cosa ha ottenuto. Non serve alcuna chiave API e non serve PostgreSQL: al
termine lascia la porta indicata, se vuoi aprire la console e rifare le prove a mano. Se una verifica
non è conforme lo script esce con codice 1 e dice quale.

Le verifiche coprono le nove della consegna: la bolletta riconosciuta con la confidenza dichiarata, la
causale sconosciuta che torna `OTHER` invece di silenzio, la valuta scritta male che viene respinta
nominando il campo, il CSV da 200 righe con il conto esatto, le sette righe scartate ognuna con il suo
numero e un codice diverso, la busta unica su tre origini d'errore diverse, l'assenza di traceback e
percorsi nelle risposte, il rilancio che non raddoppia, e l'apertura della console.

## Import movimenti da CSV

`POST /api/ai/movements/import` accetta un file CSV con intestazione esatta
`date,description,amount,currency` e risponde con quante righe sono state importate, quante righe
aveva il file e l'elenco delle scartate, ognuna con il proprio numero di riga e il motivo.

```bash
curl.exe -X POST http://localhost:8000/api/ai/movements/import -F "file=@data/movements_sample.csv"
```

```json
{
  "imported_count": 18,
  "total_rows": 25,
  "replayed": false,
  "problems": [
    {
      "row": 8,
      "reason": "amount: deve essere maggiore di zero (valore ricevuto: -180.00)",
      "detail": {
        "field": "amount",
        "code": "NOT_POSITIVE",
        "message": "deve essere maggiore di zero",
        "expected": "> 0",
        "received": "-180.00"
      }
    }
  ]
}
```

`row` è il numero di riga **nel file**, con l'intestazione che occupa la riga 1: la dodicesima riga di
dati è la tredicesima del file. È il numero che lo sportello cerca aprendo l'estratto.

#### Scelta: import parziale, 193 su 200

Un file da 200 righe di cui 7 non conformi viene risposto con `total_rows: 200`, `imported_count: 193` e
7 problemi. Le tre righe della decisione:

- **`imported_count + len(problems) == total_rows`**, sempre. È la proprietà che rende il conto
  verificabile a occhio: se tornassero meno numeri di quanti ce n'erano, il chiamante saprebbe che
  qualcosa è sparito, e potrebbe chiedere. Senza `total_rows` questa somma non si potrebbe nemmeno
  scrivere.
- **200 è la soglia che rende visibile l'errore**: sotto, 6 scarti su 20 si confondono con il rumore
  del file; sopra, la verifica costa tempo senza aggiungere informazione. La proprietà è aritmetica,
  quindi il test la dimostra a 200 righe generando il file, non a 25 righe di esempio che dimostrerebbero
  solo che quell'esempio è giusto.
- **200 righe non le tratto come tutte uguali**: 193 importate e 7 scartate, con sette cause diverse
  (`NOT_POSITIVE` due volte, `EMPTY`, `NOT_A_DATE`, `NOT_A_CURRENCY`, `NOT_A_NUMBER`,
  `COLUMN_COUNT_MISMATCH`). Se la validazione dicesse sempre "riga non valida", il conteggio tornerebbe
  uguale e il conto sarebbe comunque falso: quello che serve sapere è *quale* problema blocca *quale*
  riga.

Una riga con più problemi resta **una** voce in `problems`, non una per errore: la riga è una, e lo
sportello la sistema una volta. Il `detail` indica il primo problema da cui cominciare, e a quel punto
la stessa riga torna con il successivo. Un file che non è un CSV, un file vuoto o un file illeggibile
non producono mai un `500`: sono errori di dominio e tornano nella busta unica `ErrorResponse`
(`timestamp`, `status`, `error`, `message`, `path`, `details`) con i codici `INVALID_CSV_HEADER`,
`EMPTY_IMPORT_FILE` e `IMPORT_FILE_NOT_CSV`. Un file oltre 5 MB viene rifiutato con
`413 IMPORT_FILE_TOO_LARGE` durante la lettura, non dopo.

#### Estensione dichiarata: `Idempotency-Key` sul rilancio

L'import è stateless e non scrive sul database, quindi il pericolo del giorno 2 non è la duplicazione
in tabella: è che lo sportello, non vedendo un effetto, prema "Importa" e non sappia se la prima
volta è andata. Inviare l'intestazione `Idempotency-Key` dichiara l'intenzione:

- stesso file e stessa chiave → la stessa risposta con `replayed: true`, senza raddoppiare nulla;
- file diverso e stessa chiave → `409 IDEMPOTENCY_KEY_CONFLICT`, perché restituire il conto di un file
  diverso sarebbe peggio che fallire: lo sportello vedrebbe un totale che non è il suo.

Il registro tiene l'hash SHA-256 del contenuto e vive **nella memoria del processo**: si perde al
riavvio e non è condiviso tra più worker. È la forma giusta per una promessa dichiarata dal
chiamante su un endpoint che non scrive, non per un endpoint che scrive: quando l'import scriverà in
tabella, la chiave dovrà diventare una colonna con vincolo di unicità. Il punto è riportato anche in
`docs/ai-review/G2.md` (rilievo 12 e 16).

La prova dell'estensione è
`tests/test_import_idempotency.py::test_ricaricare_lo_stesso_file_con_la_stessa_chiave_non_raddoppia_il_conto`.

## Una busta d'errore sola

Ogni errore dell'API, di qualunque origine, esce nella stessa busta:

```json
{
  "timestamp": "2026-09-28T10:34:54.066513Z",
  "status": 422,
  "error": "VALIDATION_ERROR",
  "message": "Richiesta non valida",
  "path": "/api/ai/categorize",
  "details": [
    {
      "field": "amount",
      "code": "NOT_POSITIVE",
      "message": "deve essere maggiore di zero",
      "expected": "> 0",
      "received": "-10"
    }
  ]
}
```

Questo include il `422` che FastAPI genera da solo, senza che nessuno lo chieda: ha un handler
esplicito in `src/main.py` che usa lo stesso vocabolario di `pydantic.ValidationError`. `field` e `code`
sono la parte che un programma usa, `message` è per chi legge: se il codice non ci fosse, l'unico modo
di distinguere un importo a zero da una valuta sbagliata sarebbe leggere la frase e riconoscerla a
orecchio, e i problemi del sistema non potrebbero essere contati da un programma.

Le risposte d'errore non contengono mai un traceback, un percorso del computer o un nome di modulo.

## Tracciabilità delle richieste

Ogni risposta riporta `X-Request-Id` (se la richiesta ne porta uno, viene ecoato) e `X-Process-Time`.
Il CORS è chiuso per default: risponde solo alle origini elencate in `CORS_ORIGINS`, con metodi e
header dichiarati esplicitamente.

## Come funziona

- `/api/chat` (main + RAG): il messaggio viene embeddato, si cercano k chunk simili in pgvector e
  il contesto recuperato viene passato al modello con le fonti numerate `[1]`, `[2]`.
- `/api/advice`: genera un piano d'azione con struttura fissa e controlla che ogni affermazione sia
  supportata da una citazione (allineamento, astensione).
- `/api/categorize`: classifica la transazione in una delle 6 categorie previste e valuta la
  confidenza.
- `/api/ai/movements/import`: valida un CSV di movimenti riga per riga con i contratti Pydantic e
  restituisce quante righe sono valide e perché le altre no.
- `/api/ai/accounts`: conti e saldo. I movimenti di un conto arrivano con l'intestatario dallo stesso
  JOIN, e il riepilogo del periodo porta anche il totale per mese. Registrare un movimento scrive il
  movimento e sposta il saldo nella stessa transazione.
- Console web statica in `web/`, servita dalla stessa origin (nessun CORS necessario).

## Struttura del progetto

```
src/
  main.py              # app FastAPI, health, handler errori
  config.py            # settings tipizzate (Pydantic Settings, SecretStr, fail-fast, CORS)
  api/                 # router: chat, advice, categorize, accounts, movements
  db/                  # modelli SQLAlchemy, sessione, repository, seed, vettori pgvector
  llm/                 # factory, client OpenAI/Anthropic, embedding client
  rag/                 # chunking e retrieval ibrido
  services/            # logica applicativa: chat, advice, categorize, ingest, import, movements
  schemas/             # modelli di richiesta/risposta
  types/               # contratti condivisi (movements import, accounts)
  prompts/             # prompt su file, versionati e ispezionabili
  middleware.py        # request id, tempi di risposta, CORS
tests/                 # test unitari e di contratto
scripts/               # smoke delle giornate, benchmark asyncio (sync vs async), ingest
data/                  # CSV di esempio per l'import movimenti
docs/ai-review/        # review G1, G2 e G3
docs/recap-g1-v3.md    # appunti di studio G1
docs/recap-g2-v3.md    # appunti di studio G2
docs/recap-g3-v3.md    # appunti di studio G3
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
| Dati di prova (G3) | `uv run python -m src.db.seed --reset` |
| Verifiche G2 | `uv run python scripts/day2_smoke.py` |
| Verifiche G3 | `uv run python scripts/day3_smoke.py` |
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
| `CORS_ORIGINS` | `http://localhost:4200` | origini autorizzate, separate da virgola |
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

## Verifiche del gate G2 v3

Le nove verifiche della consegna si eseguono con un comando solo:
`uv run python scripts/day2_smoke.py`. Lo script avvia l'app su una porta libera e interroga l'API via
HTTP, non tramite oggetti Python in-process: quello che stampa è ciò che vede `curl` o il browser.

| # | Criterio | Esito |
| --- | --- | --- |
| 1 | Una bolletta viene classificata con la confidenza dichiarata | OK, `UTILITIES` con confidenza 0.90 e le parole trovate in `reasoning` |
| 2 | Una causale sconosciuta non torna silenzio | OK, `OTHER` con confidenza 0.10 e fallback dichiarato |
| 3 | Una valuta scritta male viene respinta nominando il campo | OK, `422` con `NOT_A_CURRENCY` su `currency` |
| 4 | Un CSV da 200 righe ha il conto esatto | OK, `total_rows: 200`, `imported_count: 193`, 7 problemi |
| 5 | Ogni riga scartata ha il suo numero e un codice | OK, righe `[13, 58, 100, 142, 167, 189, 200]` |
| 6 | Le sette scarti hanno sette cause diverse | OK, sette codici distinti |
| 7 | 422 di FastAPI, 400 di dominio e file vuoto: una sola busta | OK, stesse sei chiavi e stessi cinque campi in `details[]` |
| 8 | Nessuna risposta d'errore contiene traceback o percorsi | OK, quattro errori di origine diversa |
| 9 | Ricaricare non raddoppia; cambiare file con la stessa chiave è un 409 | OK, `replayed: true` e `409 IDEMPOTENCY_KEY_CONFLICT` |

In più: la console si apre e contiene le due card con il campo `Idempotency-Key`.

La prova dell'estensione dichiarata è un solo test:

```bash
uv run pytest tests/test_import_idempotency.py::test_ricaricare_lo_stesso_file_con_la_stessa_chiave_non_raddoppia_il_conto
```

Dimostra che ricaricare lo stesso file con la stessa chiave restituisce lo stesso conto e si dichiara
come rilancio (`replayed: true`). Gli altri test coprono il contratto di base della giornata.

La review completa, un rilievo per riga e il punto che decido di non correggere, è in
`docs/ai-review/G2.md`.

## La giornata 3: conti e movimenti

Il giorno 2 validava un CSV e lo rispediva indietro, senza scrivere niente. Il giorno 3 mette quei
movimenti dentro PostgreSQL e li rende interrogabili dal banco.

```bash
docker compose up -d db
uv run alembic upgrade head
uv run python -m src.db.seed --reset    # tre conti e 42 movimenti su 90 giorni
uv run python scripts/day3_smoke.py     # le nove verifiche, compresa quella dopo il riavvio
```

### Le rotte

| Rotta | Cosa risponde |
| --- | --- |
| `GET /api/ai/accounts` | i conti con il saldo |
| `GET /api/ai/accounts/{id}` | un conto e il suo saldo |
| `GET /api/ai/accounts/{id}/movements` | i movimenti **con l'intestatario e l'IBAN**, dallo stesso JOIN |
| `GET /api/ai/accounts/{id}/summary?date_from=&date_to=` | entrate, uscite, netto e il totale raggruppato per mese |
| `POST /api/ai/accounts/{id}/movements` | registra un movimento e sposta il saldo nella stessa transazione |

Nessuna di queste rotte costruisce una `select()`: prendono una sessione, ci mettono dentro un
`MovementService` e gli chiedono il risultato. Se un endpoint sa scrivere SQL, il confine fra "cosa il
banco vuole" e "come si arriva ai dati" non esiste più, e il giorno dopo non c'è più nessun posto dove
mettere la transazione.

### Tre scelte, e perché

- **Il denaro è `Decimal`/`NUMERIC(14,2)`, mai `float`.** In JSON gli importi sono **stringhe**
  (`"1351.75"`), perché un numero JSON non ha decimali: rileggendo `97.50` come `float` e
  riscrivendolo si arriva a `97.49999999999999`. La stringa toglie il passaggio, non lo nasconde. Il
  test `test_il_totale_torna_al_centesimo` mette sette commissioni da `0.10` e controlla che tornino
  `-0.70` esatto e che il tipo sia `Decimal`: in `float` farebbero `-0.7000000000000001`.
- **`accounts.balance` è denormalizzato, e i movimenti sono la verità.** Il saldo è la domanda che lo
  sportello fa a ogni istante e non può aspettare una `SUM` su tre mesi di righe. Il prezzo è che la
  copia può divergere, e si paga con una sola regola: **ogni conto apre a `0.00` e ogni variazione passa
  da `MovementService.record`**, che scrive entrambe le cose in un `commit` solo. Da lì l'invariante
  `balance == SUM(movements.amount)`, che `MovementService.reconcile` verifica con una query e non con
  la fiducia.
- **Il `commit` sta nei servizi, mai nei repository.** I repository fanno `flush` e `refresh` e non
  sanno cosa sia una transazione. Non è una questione di stile: con il `commit` dentro
  `ChatRepository.add_message`, la domanda dell'utente entrava al primo salvataggio e la risposta
  poteva non arrivare mai, lasciando in database una domanda senza risposta. Il rollback non aveva più
  niente da disfare, perché la prima scrittura era già uscita dalla transazione. Il test
  `test_un_turno_di_chat_rotto_non_lascia_la_domanda_nel_database` fa fallire il provider a metà turno
  e controlla che non resti niente.

L'estensione dichiarata del giorno è che una transazione attraversa due repository: il saldo e il
movimento si scrivono insieme o non si scrivono. Il test che lo dimostra fa rifiutare il movimento a
Postgres dal `CHECK` `ck_movements_amount_not_zero` **dopo** che l'`UPDATE` del saldo è già partito, e
poi rilegge da un'altra connessione: leggere dalla stessa sessione non proverebbe niente, perché
vedrebbe comunque il valore vecchio ancora in memoria.

## Verifiche del gate G3 v3

```bash
uv run python scripts/day3_smoke.py
```

| # | Criterio | Esito |
| --- | --- | --- |
| 1 | I conti esistono e il saldo è una stringa decimale | OK, `["1351.75", "4216.31", "2234.41"]` |
| 2 | Movimenti e intestatario vengono dallo stesso JOIN | OK, `count=10` e 10 righe, `holder` nella stessa risposta |
| 3 | Ogni movimento porta il conto giusto e due decimali | OK |
| 4 | Il riepilogo per mese raggruppa davvero | OK, 3 righe su 10 movimenti, somma dei mesi = `total_out` |
| 5 | Netto, entrate e uscite sono coerenti fra loro | OK, `1351.75 = 2500.00 - 1148.25` |
| 6 | Registrare un movimento risponde 201 col saldo nuovo | OK, `balance="1349.25"`, 10 → 11 righe |
| 7 | Un movimento da `0.00` è un 422 e non scrive niente | OK, 11 → 11 righe |
| 8 | Un turno di chat scrive la sessione e risponde | OK |
| 9 | **Un processo nuovo** ritrova chat e saldo | OK, ruoli `["user", "assistant"]`, saldo `1349.25` da entrambe le parti |

L'ultima verifica è l'unica che gli altri test non possono fare bene da soli. Le prime otto girano
dentro un server avviato dallo script; la nona gira in un **processo Python separato**, lanciato come
subprocess dopo che il primo è stato chiuso: un Python nuovo, un engine nuovo, nessuna memoria
condivisa. Se la chat e il saldo ci sono anche lì, è PostgreSQL a ricordarli, non il processo.

Unico trucco dichiarato: per non chiamare un'API a pagamento, il provider LLM viene sostituito con uno
stub locale via `app.dependency_overrides`. HTTP vero, sessione vera, transazione vera; lo stub cambia
chi risponde, non cosa viene scritto.

La review e il rilievo che decido di non correggere sono in `docs/ai-review/G3.md`.


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
  Gate G2 v3 verificato: contratti Pydantic v2, `response_model`, errori centralizzati, import CSV,
  request id e CORS da configurazione.
- **Milestone 3 - Qualità (COMPLETATA)**: test suite, eval, CI, review.
- **Giornata 3 - Conti e movimenti (COMPLETATA)**: `accounts` e `movements` su PostgreSQL, JOIN e
  aggregazioni, saldo denormalizzato con riconciliazione, e il `commit` spostato dai repository ai
  servizi. Gate G3 v3 verificato con `scripts/day3_smoke.py`, compresa la verifica dopo il riavvio in
  un processo separato.
- **Milestone 4 - Hardening**: auth JWT reale, rate limit, osservabilità, budget enforcement.
- **Milestone 5 - Produzione**: deploy, monitoraggio, documentazione operativa.

## Note

Progetto didattico. Le valutazioni quantitative (eval su dataset) sono indicative e vanno
rivalidate prima di qualsiasi uso in produzione.

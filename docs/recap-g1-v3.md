# Recap e appunti di studio — Gate G1 (Bootcamp Python AI-Powered v3)

Data: 2026-09-25 · Progetto: `lipari-bank-ai` · Obiettivo: verificare il progetto contro le lezioni
G1, integrare ciò che mancava e fissare i concetti per lo studio.

---

## 1. Punto di partenza: cosa c'era e cosa mancava

Audit iniziale del repository (molte lezioni erano già state integrate in precedenza):

| Lezione | Stato iniziale | Azione |
| --- | --- | --- |
| Tool `uv` | Presente, `uv.lock` tracciato | Solo irrobustimento in CI |
| `pyproject.toml` | Tool di sviluppo come dipendenze "extra"; `pydantic[email]` inutilizzato | Corretto |
| `.env` / `.env.example` | `Settings` senza `ENVIRONMENT`, `.env.example` incompleto | Corretto |
| Segreti | `api_key: str` semplice, nessun controllo al boot | Corretto |
| Configurazione fail-fast | Traceback Pydantic grezzo, in inglese | Corretto |
| `/health` | Sempre `200 {"status": "UP"}` | Corretto |
| Tipi | `mypy --strict` con override per `tests.*` e 5 errori reali | Corretto |
| Async | Già presente (`asyncio.gather`, `to_thread`, benchmark dedicato | Nessuna modifica di merito |
| Protocol | Mancava per l'embedding client | Corretto |
| Qualità | Solo CI degli eval a pagamento, `pytest` eseguiva anche gli eval | Corretto |
| Riproducibilità | README non sufficiente per uno estraneo | Corretto |
| Review G1 | Assente | Creata (`docs/ai-review/G1.md`) |

---

## 2. Le modifiche, file per file

### `pyproject.toml`

- Tool di sviluppo spostati in `[dependency-groups] dev` (PEP 735) invece di `[project.optional-dependencies]`.
- Rimossa la dipendenza `pydantic[email]`: nel progetto non c'è nessun `EmailStr`, quindi era pura zavorra.
- Rimossa la riga `[[tool.mypy.overrides]] module = "tests.*" disallow_untyped_defs = false`: i test ora
  sono tipizzati come il resto del codice.
- `addopts = "-m 'not eval'"`: il `pytest` normale non spende soldi in API.

### `src/config.py`

- `Environment = Literal["local", "test", "staging", "production"]` (`src/config.py:6`).
- `openai_api_key` / `anthropic_api_key` diventano `SecretStr | None`: opzionali all'avvio, obbligatorie
  solo quando il provider viene usato.
- `load_settings()` (`src/config.py:60`) intercetta `ValidationError` e la riformula in
  `ConfigurationError` con `CAMPO: cosa ci si aspetta`, usando `include_input=False` per non poter
  stampare mai il valore ricevuto.
- `secret_is_configured()` (`:75`) distingue "vuota" da "impostata"; `require_secret()` (`:79`) è l'unico
  punto in cui un segreto torna in chiaro.

### `src/main.py`

- `build_health_response()` (`:97`) costruisce uno stato onesto: `UP` solo se tutte le credenziali sono
  presenti, `DEGRADED` altrimenti, con l'elenco delle variabili mancanti.
- `health()` (`:125`) restituisce HTTP 503 quando lo stato è `DEGRADED`.
- Modelli di risposta espliciti (`CredentialsHealth`, `HealthResponse`) invece di dizionari sciolti.

### Altrove

- `src/llm/embedding_client.py:23`: nasce `EmbeddingClientProtocol`; `retrieval_service.py:17` e
  `ingest_service.py:10` dipendono dal protocollo, non dalla classe concreta.
- `src/llm/factory.py:28` e `src/api/categorize.py:27`: la chiave si chiama con `require_secret()`.
- `.env.example`: ogni variabile di `Settings` è elencata con una riga di commento e senza valori reali.
- `.github/workflows/eval.yml`: nuovo job `quality` su push/PR; l'eval a pagamento resta solo su
  schedule e dispatch manuale.
- `README.md` riscritto: quickstart, scelta dipendenze, semantica di `/health`, tempi di ricostruzione.

---

## 3. Cosa ho imparato dalle lezioni (la parte che conta per lo studio)

### 3.1 Dipendenze e lockfile

- Il lockfile (`uv.lock`) è il contratto tra sviluppatori: senza, "funziona sulla mia macchina" è una
  frase senza significato. Deve finire in Git.
- `--locked` in CI significa "non risolvere, applica": se il lockfile non è allineato a
  `pyproject.toml` il job fallisce invece di installare versioni diverse da quelle dichiarate.
- Le dipendenze di sviluppo (pytest, mypy, ruff, alembic) non sono dipendenze del pacchetto che
  finiscono in produzione: per questo esiste il dependency group PEP 735, che `uv add --dev` gestisce
  nativamente e che `uv sync` installa per default. In produzione: `uv sync --no-dev --locked`.

### 3.2 Configurazione e ambiente

- I segreti vivono solo in `.env` (mai nel codice, mai nel lockfile, mai nei log). `.env` è nel
  `.gitignore`, `.env.example` no: è il documento che dice al collega cosa compilare.
- **Fail-fast** significa morire subito e con un messaggio che dice *cosa* correggere. Un
  `ValidationError` di Pydantic grezzo è tecnicamente corretto ma inutile per chi deve operare.
  Nota di sicurezza: nel messaggio di errore non si include l'input, altrimenti un errore di
  validazione stamperebbe il segreto stesso.
- Le chiavi provider sono opzionali **all'avvio** per una ragione precisa: se fossero obbligatorie,
  l'unico comando che potresti usare per capire cosa manca (`/health`) non esisterebbe. La chiave
  diventa obbligatoria nel momento in cui serve.

### 3.3 Tipi, `Protocol` e falsi

- `mypy --strict` non è un vezzo: disattiva implicitamente i `Any` e costringe a dichiarare cosa
  entra e cosa esce da ogni funzione. Soprattutto nei test, dove i falsi sono il punto debole.
- Un `Protocol` descrive *cosa serve*, non *cosa è*: `RetrievalService` ha bisogno di un oggetto con
  `embed()`, non di `SentenceTransformerEmbeddingClient`. Il fake del test implementa il protocollo e
  nessuno deve più conoscere il dettaglio dell'implementazione.
- Attenzione all'errore classico di questo capitolo: annotare una variabile con qualcosa che non è
  un tipo (`stmt: text`, dove `text` è la *funzione* `sqlalchemy.text`) fa fallire mypy; il tipo vero
  era `TextClause`.

### 3.4 Async

- L'async serve quando **si aspetta**: I/O lento (DB, API, LLM). Su micro-query locali il guadagno
  sparisce, come misurato in `scripts/async_vs_sync.py` e riportato in
  `docs/async-vs-sync-experiment.md`.
- `asyncio.gather` = Avvio N coroutine e attendo tutte. `await` = sospendi finché questo finisce.
- Codice di blocco dentro una coroutine blocca l'event loop: due esempi, `time.sleep` (mai) e
  `asyncio.to_thread` percodice sincrono che non ha versione async (lettura file negli eval:
  `src/eval/runners.py:56`).

### 3.5 Health veritiero

- Un health check che risponde sempre `200` non è un monitor, è una decorazione. Se qualcosa manca,
  deve dirlo: nel nostro caso `503` + elenco delle variabili mancanti, senza mai i valori.
- Veritiero significa anche distinguishere "il processo è vivo" (sì) da "può lavorare" (no, manca la
  chiave). Due informazioni diverse, un solo endpoint.

### 3.6 Qualità automatizzata

- "La qualità non è una promessa" significa che deve esserci un comando, e il comando deve girare
  anche senza che qualcuno ci pensi: da qui il job `quality` in CI su ogni push.
- I test che costano soldi (eval con API reali) vanno separati da quelli gratuiti: default veloce,
 Marker `eval` per lanciarli quando serve.

### 3.7 Riproducibilità

- Ho cancellato `.venv` e ricreato l'ambiente da zero: **~17 secondi** con cache `uv` calda
  (93 pacchetti). Dalla cache vuota il primo sync scarica anche torch e transformers.
- Il primo ostacolo per un collega **non è tecnico**: è creare `.env` con `JWT_SECRET` e le chiavi
  API. Senza, l'app si ferma con il messaggio fail-fast (che è esattamente il comportamento voluto).
- Su Windows, se la console non mostra le lettere accentate nei messaggi: `chcp 65001`.

---

## 4. Verifiche eseguite (comando → esito)

| Comando | Esito |
| --- | --- |
| `uv lock` | 114 pacchetti risolti, `email-validator` e `dnspython` rimossi |
| `uv lock --check` | OK, lockfile allineato |
| `uv run ruff format --check .` | 67 file già formattati |
| `uv run ruff check .` | All checks passed |
| `uv run mypy src tests scripts alembic/env.py` | **54 file, 0 errori** in strict |
| `uv run pytest tests/test_health.py tests/test_types.py tests/test_chunking.py` | 30 passed |
| `uv run pytest --collect-only -q` | 47/49 raccolti, 2 deselezionati (eval) |
| `uv run pytest --collect-only -q -m eval` | 2/49 raccolti: l'override `-m eval` funziona |
| Avvio senza `DATABASE_URL`/`JWT_SECRET` | exit 1, `Configurazione non valida... DATABASE_URL: è obbligatoria; ... JWT_SECRET: è obbligatoria` |
| `MAX_TOKENS_PER_REQUEST=molte` | exit 1, `MAX_TOKENS_PER_REQUEST: deve essere un numero intero` |
| `/health` senza chiavi | **503** + `status: DEGRADED` + `missing: [OPENAI_API_KEY, ANTHROPIC_API_KEY]`, nessun valore |
| `uv venv --clear` + `uv sync --locked` | 17,4 s, 93 pacchetti |

**Non eseguito**: la suite completa (47 test) richiede PostgreSQL; al momento della verifica Docker
Desktop non era avviato, quindi ho eseguito i 30 test che non toccano il DB. Il job `quality` in CI
copre il resto con un PostgreSQL + pgvector come servizio: al primo run su GitHub va controllato che
passi.

---

## 5. Debug: cosa è andato storto e cosa mi ha insegnato

**I 5 errori mypy iniziali** (in `scripts/async_vs_sync.py` e `tests/test_advice.py`) e i problemi
emersi nel controllo strict dei test:

1. `stmt: text` — `text` è una funzione, non un tipo. Tipo corretto: `TextClause`
   (`scripts/async_vs_sync.py:25,32,41`).
2. `def fake_embedding_client() -> FakeEmbeddingClient` con `yield` dentro — una funzione che fa
   `yield` restituisce un generatore: il tipo di ritorno è `Generator[FakeEmbeddingClient, None, None]`
   (`tests/test_advice.py:33`).
3. `created_at="2024-01-01T00:00:00Z"` su un campo `datetime` — Pydantic validava, mypy no: meglio
   passare `datetime(2024, 1, 1, tzinfo=UTC)` (`tests/test_types.py:37`).
4. Costruttore usato per testare un valore **non valido**: `CategorizeResponse(category="INVALID", ...)`
   è un errore di tipo, non di validazione. Il modo giusto è `model_validate({...})` da un dict
   (`tests/test_types.py:110`): costruisci la validazione negativa con l'API giusta.

**Errori miei durante il lavoro** (buon materiale di studio):

- Ho scritto `main_module.settings` nei test: mypy ha risposto
  `Module "src.main" does not explicitly export attribute "settings"`. Le impostazioni sono definite in
  `src.config.py`: si importa da lì (`from src.config import settings`), non si aggira il modulo che
  le riusa.
- Il traceback del fail-fast mostrava `�` al posto di `è`: non era un bug del codice ma la console
  Windows in codepage legacy quando l'output viene rediretto. `chcp 65001` lo risolve.

---

## 6. Recupero delle basi Python (la parte da studiare davvero)

### 6.1 Strutture dati, comprehension, f-string

- **Comprehension** = costruire una lista/dizionario/set con un `for` dentro le parentesi. Nel progetto:
  `return [json.loads(line) for line in raw.splitlines() if line.strip()]`
  (`src/eval/runners.py:38`) — con filtro `if` in coda. È più corta e più veloce di append in un
  loop, e con mypy il tipo si inferisce da solo.
- **f-string** = `f"..."` con espressioni dentro le `{}`. Esempio dal progetto:
  `f"Risposta: {text[:50]}..."`. Con `=` si può stampare anche il nome della variabile
  (`f"{x=}"`), e `:.<precision>`, `:>larghezza`, `:.0%` controllano il formato.
- Dizionari e set servono per lookup (`missing: list[str]` in `/health`) e per deduplicare; le tuple
  sono immutabili e vanno bene per chiavi e valori di ritorno multipli.

### 6.2 Classi, decoratori, `with`, `yield`

- **Classe** = dati + comportamento. `Settings` incapsula la configurazione; `HealthResponse` garantisce
  la forma della risposta HTTP.
- **Decoratore** = funzione che avvolge un'altra funzione. `@llm_retry(...)` in
  `src/llm/retry.py:18` (retry con backoff esponenziale sulle API esterne) e `@asynccontextmanager` in
  `src/main.py:20` (lifespan di FastAPI) sono due esempi reali. Un decoratore è una funzione che
  riceve la funzione e restituisce la funzione (avvolta).
- **`with`** = gestione automatica delle risorse: `with open(...)` chiude il file anche se il corpo
  solleva. In async, `async with session_factory() as session` chiude la sessione e rilascia la
  connessione al pool.
- **`yield`** = "produco valori uno alla volta, ma non subito": è così che funzionano i generatori.
  `get_db` in `src/db/session.py` è un generatore asincrono di sessioni: FastAPI lo consuma, chiude la
  sessione e libera la connessione al termine della richiesta. Attenzione: la firma di una funzione con
  `yield` è `Generator[T, None, None]`, non `T` (errore n. 2 sopra).

### 6.3 Eccezioni, traceback, debugger

- `try / except / else / finally`: si cattura **quello che ti aspetti**, non tutto con `except
  Exception` (e lì serve un commento per giustificare la scelta).
- `raise ... from None` in `load_settings()`: rende l'errore leggibile e taglia la catena di cause
  che porta al traceback interno di Pydantic.
- Il **traceback** si legge dal basso in alto: l'ultima riga è dove è esplosa l'eccezione, le righe sopra
  sono lo stack. Nel fail-fast la riga finale era
  `src.config.ConfigurationError: Configurazione non valida...` con exit code 1.
- **Debugger**: `breakpoint()` in una riga sospetta avvia la REPL locale con il contesto del frame
  (`p`, `type(p)`, `p.__dict__`). Utili due casi della sessione: dentro `load_settings()` per vedere
  com'è fatto l'errore Pydantic (`exc.errors(include_input=False)`), e dentro `build_health_response()`
  per controllare cosa arriva davvero da `SecretStr` senza stamparlo.

---

## 7. Errori da non ripetere

1. Health check che risponde sempre `OK`.
2. Segreti in chiaro nelle variabili Python o nelle risposte HTTP.
3. `except Exception` per nascondere un bug invece di gestire l'errore atteso.
4. Override di mypy per far passare i test: si nascondono i bug, non li risolvono.
5. Dipendenze inutili (`pydantic[email]`, `pytest` in produzione).
6. Test con costruttori che falliscono per typing invece che per validazione.
7. Credere che `.env.example` possa contenere valori: anche una password di sviluppo nel file
   tracciato diventa un seggetto da ruotare.

---

## 8. Quiz di verifica (risposte in fondo)

1. Perché `uv.lock` deve essere committato?
2. Cosa cambia, in pratica, tra `[project.optional-dependencies].dev` e `[dependency-groups].dev`?
3. Perché le chiavi API sono opzionali in `Settings` ma obbligatorie in `require_secret()`?
4. Cosa-fa `SecretStr` rispetto a una `str`?
5. Perché `exc.errors(include_input=False)`?
6. `raise ConfigurationError(...) from None` cosa cambia?
7. Cosa serve `Protocol` se esiste già la classe?
8. Perché una fixture con `yield` si tipizza come `Generator[...]`?
9. `asyncio.gather` vs `await` in loop: differenza?
10. Quando `asyncio.to_thread` è la risposta giusta?
11. Qual è la differenza tra "il processo è vivo" e "l'app può lavorare"?
12. Quanto costa rifare l'ambiente da zero e cosa cambia con la cache fredda?

**Risposte**

1. Fissa versioni e dipendenze transitive: rende l'ambiente riproducibile su ogni macchina e in CI.
2. Le extra diventano dipendenze *del pacchetto* (installabili, ma con altra sintassi e priorità
   diverse); il dependency group PEP 735 le tratta come tooling di sviluppo: `uv add --dev` funziona
   e `uv sync` le installa per default.
3. Così l'app parte e `/health` può dire cosa manca; la chiave serve davvero solo quando si chiama
   il provider, e lì l'errore è esplicito.
4. `repr`, `str` e serializzazione mostrano `**********`: il segreto esce solo con
   `get_secret_value()`.
5. Per non stampare il valore ricevuto: un errore di validazione su un segreto altrimenti lo
   mostrerebbe nei log.
6. Rimuove la catena di cause: l'utente vede solo il messaggio leggibile, non il traceback interno.
7. A separare il "contratto" dal "contenuto": il servizio dichiara ciò che usa e i test possono
   passare un fake senza dipendere dall'implementazione reale.
8. Perché `yield` trasforma la funzione in un generatore: restituisce un oggetto iterabile, non il
   valore finale.
9. `await` in un `for` esegue una dopo l'altra (la slow cornice non nasconde l'attesa); `gather` le
   avvia tutte e le sovrappone.
10. Quando il codice è sincrono e bloccante e non ha una versione async (I/O su file, librerie
    sync): `to_thread` lo sposta in un thread senza bloccare l'event loop.
11. Il primo è l'HTTP 200 del processo; il secondo richiede DB, chiavi e modelli: da qui
    `200 UP` vs `503 DEGRADED`.
12. ~17 s con cache `uv` calda (93 pacchetti); dalla cache vuota il tempo è dominato dal download di
    torch e transformers, quindi nettamente maggiore.

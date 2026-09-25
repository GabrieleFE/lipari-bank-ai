# Recap e appunti di studio — Gate G2 (Bootcamp Python AI-Powered v3)

Data: 2026-09-25 · Progetto: `lipari-bank-ai` · Obiettivo: verificare il progetto contro le lezioni
G2 (contratti Pydantic v2, `response_model`, router per feature, Dependency Injection, errori
centralizzati, middleware, handler async/sync) e fissare i concetti per lo studio.

---

## 1. Punto di partenza: cosa c'era e cosa mancava

| Lezione G2 | Stato iniziale | Azione |
| --- | --- | --- |
| Contratti Pydantic v2 | `CategorizeRequest/Response` conformi, nessun contratto per i movimenti | Aggiunto `src/types/movements.py` |
| `response_model` | Presente su tutti gli endpoint esistenti | Verificato, aggiunto sul nuovo |
| Router per feature | Già `api/chat.py`, `api/advice.py`, `api/categorize.py` | Aggiunto `api/movements.py` |
| Dependency Injection | Già presente con `Depends()` e `Protocol` per l'embedding client | Esteso al nuovo servizio |
| Errori centralizzati | `AppError` + handler per `Exception` e `RequestValidationError`, nessun `HTTPException` in `src` | Esteso con `ImportFileError` |
| Middleware CORS | Lista fissa nel codice | Ora da `settings.cors_origins` |
| Middleware request id | Assente | Aggiunto con `X-Process-Time` |
| Handler async vs sync | Endpoint di sola I/O, tutto `async` | Invariato, motivato in §7 |
| Dipendenze upload | `python-multipart` assente | Aggiunto |
| Dati di esempio | Nessun CSV | `data/movements_sample.csv` |
| Console web | 4 card, nessuna di import | Card "Import movimenti" |
| Review G2 | Assente | Creata (`docs/ai-review/G2.md`) |

Verifica di non-regressione fatta a mano, non a memoria: nessun `HTTPException` in `src`, nessun
leftover di Pydantic v1 (`class Config`, `.dict()`, `parse_obj`, `@validator`).

---

## 2. I tre contratti (`src/types/movements.py`)

```python
class MovementRow(BaseModel):  # :6   una riga del CSV, validata
    model_config = ConfigDict(str_strip_whitespace=True)
    date: date
    description: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ImportProblem(BaseModel):  # :21  perché una riga è stata scartata
    row: int
    field: str | None
    reason: str


class MovementImportResponse(BaseModel):  # :29  cosa risponde l'endpoint
    imported_count: int
    problems: list[ImportProblem]
```

Cosa è da studiare qui, perché sono tutte scelte e non sintassi:

- **I vincoli stanno nel modello, non nel codice.** `gt=0` e `pattern` fanno sì che una riga con
  importo `0` o valuta `eur` non arrivi mai alla business logic: se domani la validazione, il
  codice downstream può fidarsi del tipo.
- **`str_strip_whitespace=True`** normalizza prima di validare: `" "` come descrizione diventa `""` e
  viene respinta da `min_length=1`. Senza, uno spazio passerebbe la lunghezza minima.
- **`Decimal`, non `float`.** Un importo è denaro: `0.1 + 0.2` in binario non fa `0.3`, e su un
  totale di conto un centesimo fantasma è un bug che il cliente trova per primo.
- **`field: str | None`.** Non tutte le righe hanno un campo colpevole: se la riga ha 3 colonne
  invece di 4 il problema è della riga, non di un campo. Per onestà il campo vale `null`.
- **`pattern` invece di `constr`** perché con Pydantic v2 i vincoli stanno dentro `Field`: è lo stesso
  modello di `Annotated`, quindi si può riusare il tipo.

### Il dizionario dei messaggi (`movements_import_service.py:13`)

Pydantic restituisce un `type` tecnico (`greater_than`, `string_too_short`, `pattern_mismatch`), che
non serve a un utente. La mappa `_FIELD_HINTS` lo traduce in italiano e il metodo `_describe`
(`:126`) accoppia il messaggio al valore ricevuto, troncato a 50 caratteri per non stampare un file
intero dentro la risposta.

Da ricordare: `ValidationError.errors()` restituisce una lista di dict con `type`, `loc`, `msg`,
`input` e `ctx`. Il `ctx` contiene il valore che ha fatto fallire il vincolo (per esempio
`{'gt': 0}`): è la fonte pulita per il messaggio, perché non passa per la localizzazione.

---

## 3. Il servizio: perché in `services/` e non in `api/`

`MovementsImportService.import_csv(raw: bytes)` (`movements_import_service.py:47`) è una funzione
sincrona e pura che non conosce FastAPI. L'endpoint fa solo tre cose: prendere il file, chiamare il
servizio, restituire il modello.

| Decisione | Motivo |
| --- | --- |
| Servizio sincrono, non `async def` | Non c'è I/O: legge da byte già in memoria. Dichiararlo `async` farebbe consumare il loop event per un lavoro di CPU |
| Riceve `bytes`, non `UploadFile` | Non lega il servizio a FastAPI: si può testare con `b"date,..."` senza client HTTP |
| Solleva eccezioni di dominio | L'API non deve costruire risposte di errore: `ImportFileError` (`exceptions.py:38`) è tradotta una sola volta dall'handler |
| Non tiene stato | Due import dello stesso file danno lo stesso risultato |

L'ordine dei controlli dentro `import_csv` è quello del fallimento più economico: prima la
dimensione (413, `MAX_IMPORT_BYTES` a `:10`), poi la decodifica (UTF-8 con `utf-8-sig`, così un file
prodotto da Excel su Windows non perde la prima colonna per il BOM), poi l'intestazione, poi le righe.

```python
text = raw.decode("utf-8-sig")  # :68  BOM gestito
rows = list(csv.reader(io.StringIO(text), strict=True))  # :81  quoting malformato = errore
header, body = self._split(rows)  # :90  header + righe utili
self._check_header(header)  # :100 400 INVALID_CSV_HEADER
```

`strict=True` non è pignoleria: senza, una virgoletta non chiusa viene "guidata" fino alla riga
successiva e il file viene letto come se fosse valido, spostando di fatto i numeri di riga.

### I numeri di riga devono parlare dell'utente

`_split` scarta le righe vuote ma **continua a contare**: il `row` restituito è la posizione nel file
così come l'utente lo vede nel suo editor, non l'indice della lista di quelle valide. Se il numero
fosse relativo alle sole righe utili, "riga 7" del messaggio punterebbe a una riga diversa del file
e il debug partirebbe già storto.

---

## 4. L'endpoint e le tre cose che fa (`src/api/movements.py`)

```python
router = APIRouter(prefix="/api/ai", tags=["Movements"])     # :7

def get_movements_import_service() -> MovementsImportService:  # :10
    return MovementsImportService()

@router.post("/movements/import", response_model=MovementImportResponse,
             responses={400: {...}, 413: {...}, 422: {...}})   # :14
async def import_movements(file: Annotated[UploadFile, File(...)],
                           service: Annotated[MovementsImportService,
                                              Depends(get_movements_import_service)]) -> ...:  # :35
```

- **`response_model`** fa tre cose insieme: documenta la risposta in `/docs`, la **filtra** (campo non
  dichiarato non esce mai) e la **valida** in uscita. È il motivo per cui un errore di un servizio
  non può sporcare il contratto pubblico.
- **`responses={...}`** documenta i codici che il validatore non produce da solo: senza, `/docs`
  mostrerebbe solo il 422 degli errori di validazione e gli errori di dominio sarebbero invisibili.
- **`Annotated[X, Depends(...)]`** invece di `service = Depends(...)`: la dipendenza è parte del
  tipo della funzione, quindi `mypy` la controlla e l'override nei test è pulito
  (`app.dependency_overrides[get_movements_import_service] = lambda: FakeService()`).
- **Le dipendenze (`File`, `Depends`) non hanno default**: FastAPI deduce che vengono dalla
  dichiarazione. Un `= None` le renderebbe opzionali e romperebbe il 422.
- `UploadFile` è asincrono (`await file.read()`) e va chiuso: il file descriptor viene rilasciato
  quando l'oggetto viene eliminato, ma chiudere esplicitamente è più onesto in un servizio.
- Serve `python-multipart` per i form: senza la dipendenza l'errore arriva a runtime, al primo
  upload, non all'avvio. Ora è in `pyproject.toml` e nel lockfile.

---

## 5. Errori centralizzati: una sola busta, tre livelli

In `src/main.py`:

| Handler | Quando scatta | Risposta |
| --- | --- | --- |
| `AppError` (`:36`) | Errori di dominio: sessione inesistente, import file non valido, provider LLM | status e codice decisi dall'eccezione |
| `RequestValidationError` (`:67`) | La richiesta non è nemmeno valida (manca `file`, tipo sbagliato) | `422 VALIDATION_ERROR` con `details` |
| `Exception` (`:52`) | Qualunque cosa non prevista | `500 INTERNAL_ERROR`, log con traceback, risposta senza dettagli |

Tutti e tre costruiscono la stessa forma, dichiarata in un modello:

```json
{"timestamp": "...", "status": 400, "error": "EMPTY_IMPORT_FILE",
 "message": "Il file è vuoto: manca l'intestazione del CSV", "path": "/api/ai/movements/import"}
```

Tre cose da studiare in questo disegno:

1. **Il traceback va nel log, non nella risposta.** L'handler generico registra l'eccezione completa e
   risponde con un testo fisso: il cliente non viene a sapere che esiste una cartella `src/`.
2. **`details` compare solo dove ha senso.** Nel 422 è la lista dei problemi di validazione (ed è
   lì che si verifica che nomini `file`); in un errore di dominio non serve e non si inventa.
3. **`ImportFileError` ha lo status come parametro** (`exceptions.py:41`): la stessa eccezione copre
   il 400 del file non valido e il 413 del file troppo grosso, perché sono lo stesso fatto ("non posso
   elaborare questo file") con gravità diversa.

---

## 6. Middleware: ordine, tempo, CORS (`src/middleware.py`)

```python
def setup_middleware(app: FastAPI) -> None:  # :12
    @app.middleware("http")
    async def add_request_id(request, call_next):  # :14
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())  # :15
        start = time.perf_counter()  # :16
        response = await call_next(request)  # :17
        response.headers["X-Request-Id"] = request_id  # :18
        response.headers["X-Process-Time"] = f"{time.perf_counter() - start:.4f}"
        return response
```

- **L'eco, non la sostituzione.** Se il client (o un proxy) manda `X-Request-Id`, la risposta porta lo
  stesso valore: un id impersonale su una risposta e uno suo sull'altra rende inutile la correlazione.
  Se non arriva, se ne genera uno.
- **`perf_counter`, non `time.time`.** `perf_counter` è un contatore monotono ad alta risoluzione:
  non risente di cambi di ora di sistema (NTP, ora legale) e serve proprio a misurare durate.
- **`call_next` va sempre awaited**: senza, la risposta non verrebbe mai prodotta.
- **CORS dichiarativo e chiuso**: `allow_origins=settings.cors_origins`, `allow_methods` e
  `allow_headers` espliciti invece di `*`. Su un'API che gira con credenziali, `*` è una scelta che
  va evitata, e in ogni caso non è necessaria: la console di `web/` è servita dalla stessa origin e
  non attraversa CORS.

### La lista di origini dalla configurazione (`src/config.py:41`)

```python
cors_origins: Annotated[list[str], NoDecode] = Field(
    default_factory=lambda: ["http://localhost:4200"]
)


@field_validator("cors_origins", mode="before")  # :46
def split_cors_origins(cls, value: object) -> object: ...
```

`NoDecode` dice a pydantic-settings di **non** tentare di decodificare il valore JSON della variabile
d'ambiente: gli lascia arrivare come stringa al validatore. Il validatore `mode="before"` la divide
su virgola, e così `CORS_ORIGINS=http://a,http://b` in `.env` diventa una lista. È il modo giusto per
una variabile "umana" (separata da virgola) in un sistema pensato per il JSON, e mostra a cosa serve
`mode="before"`: il validatore vede il valore grezzo, prima che Pydantic provi a dargli un tipo.

---

## 7. Async vs sync: la regola, e perché qui non cambia niente

| Tipo di lavoro | Come dichiararlo | Perché |
| --- | --- | --- |
| I/O di rete o DB | `async def` + `await` | Il worker può servire altre richieste mentre aspetta |
| Lavoro di CPU breve | `def` (sincrono) | FastAPI lo esegue in un threadpool, quindi non blocca il loop |
| Lavoro di CPU lungo | `async def` + `to_thread` | Per non saturare il threadpool di default |

Nel progetto gli endpoint sono `async` perché parlano con PostgreSQL o con un provider LLM; il
servizio di import è `def` perché lavora su dati già in memoria (i byte del file), quindi non ha
nulla da aspettare. Dichiararlo `async` non lo renderebbe più veloce: renderebbe solo più onesto il
fatto che blocca il loop, e per un file da pochi kilobyte la differenza è rumore.

Il caso limite da ricordare è proprio questo endpoint: `csv.reader` è CPU pura, e un file al limite
dei 5 MB è parsing, non I/O. Qui il `to_thread` non serve (il limite di 5 MB tiene il lavoro sotto
qualche decina di millisecondi), ma se il limite salisse di ordini di grandezza la risposta
corretta sarebbe `await asyncio.to_thread(service.import_csv, raw)`. È il tipo di ragionamento che
il giorno 7 chiede di saper fare: la firma del handler (`async def` o `def`) è una dichiarazione di
intenti, non un dettaglio di stile.

---

## 8. Verifiche eseguite

| Controllo | Comando | Esito |
| --- | --- | --- |
| Lockfile | `uv lock --check` | OK |
| Formato | `uv run ruff format --check .` | OK |
| Lint | `uv run ruff check .` | OK |
| Tipi | `uv run mypy src tests scripts alembic/env.py` | 58 file, 0 errori |
| Test del contratto | `uv run pytest tests/test_movements_import.py tests/test_health.py -q` | 20 passed |
| Import valido | `curl.exe -X POST .../import -F "file=@data/movements_sample.csv"` | 200, 18/25, 7 problemi con riga e campo |
| File non CSV | `curl.exe ... -F "file=@memo.txt"` | 400 `INVALID_CSV_HEADER` |
| File vuoto | `curl.exe ... -F "file=@vuoto.csv"` | 400 `EMPTY_IMPORT_FILE` |
| Nessun file | `curl.exe -X POST .../import` | 422 `VALIDATION_ERROR`, `details: ["file: Field required"]` |
| Nessun leak | lettura delle risposte sopra | nessun traceback, nessun percorso |
| Request id | `curl.exe -H "X-Request-Id: demo-g2-001" .../health` | header ecoato |
| CORS | preflight da origine configurata e da origine ignota | consentita la prima, nessun header per la seconda |

I test che richiedono PostgreSQL non sono stati eseguiti in locale perché Docker Desktop non era
avviato; il job `quality` della CI li copre (va verificato al primo run su GitHub).

---

## 9. Cose da rifare da soli (verifica di comprensione)

1. Perché `response_model` protegge il contratto anche se un servizio sbaglia? Cosa farebbe senza?
2. Cosa cambia in `ValidationError` tra Pydantic v1 e v2, e perché `errors()` va letta per `type`/`ctx`
   invece che per `msg` (localizzazione)?
3. Perché un file non valido è `400` e non `500`? Qual è la differenza tra "errore di dominio" e
   "errore di programmazione" in questo contesto?
4. `Depends` dentro `Annotated` cambia qualcosa rispetto a `x = Depends(...)`? Cosa ne fa `mypy`?
5. Perché il CORS con `allow_origins=[]` è più sicuro di `["*"]`, e quando `*` è accettabile?
6. Se il limite dei 5 MB diventasse 500 MB, cosa cambierebbe nel codice e perché `to_thread` servirebbe?

---

## 10. Indice dei file toccati

| File | Cosa contiene |
| --- | --- |
| `src/types/movements.py` | I tre contratti del dominio import |
| `src/services/movements_import_service.py` | Validazione riga per riga, messaggi in italiano, limite 5 MB |
| `src/api/movements.py` | Endpoint `POST /api/ai/movements/import` con `response_model` e `Depends` |
| `src/exceptions.py` | `ImportFileError` (400 e 413) tra le eccezioni di dominio |
| `src/config.py` | `cors_origins` con `NoDecode` + `field_validator(mode="before")` |
| `src/middleware.py` | Eco di `X-Request-Id`, `X-Process-Time`, CORS da configurazione |
| `src/main.py` | `include_router(movements.router)` e handler errori già unificati |
| `data/movements_sample.csv` | 25 righe: 18 valide, 7 con 6 tipi di problema diversi |
| `web/index.html` | Card "Import movimenti" con `FormData` e rendering dei problemi |
| `pyproject.toml` / `uv.lock` | `python-multipart` |
| `docs/ai-review/G2.md` | Review: una riga per rilievo e il punto non corretto |

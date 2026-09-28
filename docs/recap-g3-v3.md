# Recap e appunti di studio - Gate G3 (Bootcamp Python AI-Powered v3)

Oggetto: conti e movimenti su PostgreSQL, JOIN e aggregazioni, denaro decimale, e il posto dove
mettere la transazione.

---

## 1. Punto di partenza: cosa c'era e cosa mancava

Fino al giorno 2 l'unica cosa che finiva in tabella era la chat. Il resto era una validazione: il CSV
veniva controllato riga per riga e **rispedito indietro**, senza scrivere niente. La ragione era
dichiarata ed è giusta: scrivere richiedeva prima una decisione di dominio che non era ancora stata
presa (una riga importata e una riga già presente sono la stessa entità o due?).

Il giorno 3 prende quella decisione e costruisce il tavolo su cui scriverci:

- `accounts`: il conto, con `balance` denormalizzato.
- `movements`: le righe, con FK verso `accounts` e `ondelete=CASCADE`.

E mentre le costruiva è venuto fuori il problema vero della giornata, che non era "scrivere": era
**dove sta il `commit`**.

---

## 2. Il denaro non è un numero

Il primo errore che si fa scrivendo `amount: float` è pensare che vada bene "per un progetto didattico".
Non va bene, e il motivo è che `float` non sa rappresentare `0.10`:

```
0.1 + 0.2 == 0.3        ->  False, in Python
Decimal("0.1") + Decimal("0.2") == Decimal("0.3")   ->  True
```

Non è un problema di arrotondamento visibile, è un problema di **uguaglianza**. Un saldo che non
passa il `==` con sé stesso fa fallire test che dovrebbero essere veri, e li fa fallire in modo
sbagliato: il risultato è giusto a occhio.

Da qui la scelta, in tre passi:

1. **Colonna `NUMERIC(14,2)`**, non `REAL`/`DOUBLE PRECISION`. PostgreSQL non ha un tipo decimale
   esatto nativo: `NUMERIC` è esatto e implementato in base 10, quindi `0.10` è `0.10`.
2. **`Decimal` in Python**, non `float`. Il driver restituisce `Decimal` e la colonna lo sa già.
3. **Stringhe in JSON.** Questo è il passaggio che si dimentica, e l'ho scritto nei test e negli
   smoke perché è il meno ovvio. In JSON `97.50` non è un numero: un numero JSON non ha decimali, e
   `json.loads("97.50")` dà `97.5`. Rileggendo quel `97.5` e riscrivendolo si arriva a
   `97.49999999999999`. Serializzando `Decimal` come stringa (`"97.50"`) il percorso
   `Decimal → JSON → Decimal` non attraversa la virgola mobile, e non ha dove perdere il centesimo.

La prova che non è una superstizione:

```python
# tests/test_accounts.py
# sette commissioni da 0.10
assert totale == Decimal("-0.70")
# in float: -0.7000000000000001
```

Il test controlla anche `isinstance(totale, Decimal)`, perché un `-0.70` giusto ottenuto da un `float`
per caso passerebbe il confronto e avrebbe comunque il difetto.

---

## 3. `balance` denormalizzato: la copia e la verità

Il saldo di un conto è la domanda che lo sportello fa a ogni istante. Calcolarlo con una `SUM` su tre
mesi di righe significa che ogni schermata attende una query che cresce con i dati. Quindi si scrive
il saldo accanto ai dati e si tiene la `SUM` come alternativa.

**La copia non è la verità. I movimenti sono la verità.** Il saldo si può sempre ricalcolare, e
`MovementService.reconcile` lo fa.

Il prezzo di questa scelta è che la copia può divergere dalla verità, e va detto come si paga:

- ogni conto **apre a `0.00`**;
- ogni variazione del saldo passa da `MovementService.record`, che scrive il saldo e il movimento
  **nello stesso `commit`**.

Da lì l'invariante, che ho scritto nella docstring di `Account` perché è la cosa che si deve ricordare
per prima:

```
balance == SUM(movements.amount)      per ogni conto
```

Ed è un invariante che si può **controllare**, non da cui fidarsi: `reconcile` restituisce entrambi i
numeri, e quando divergono sono due, non un messaggio.

```python
# tests/test_accounts.py
stored, recomputed = await MovementService(session).reconcile(account.id)
assert stored != recomputed  # il test costruisce la divergenza e la constata
```

Quel test scrive le righe a mano, senza passare dal servizio, quindi il saldo memorizzato resta quello
iniziale: è esattamente la situazione da cui `reconcile` deve accorgersi.

---

## 4. Dove sta il `commit` (la parte che mi ha occupato di più)

Il punto in cui ho sbagliato per primo, e che vale la pena raccontare perché non è ovvio.

I repository facevano `commit()`. Sembrava la cosa giusta: un repository che salva, salva. Ma un
`commit` chiude la transazione. E la chat scrive **due** righe per un turno: la domanda e la risposta.

```
con commit dentro ChatRepository.add_message:

  add_message(utente)      ->  BEGIN, INSERT domanda, COMMIT     <- uscita dalla transazione
  add_message(assistente) ->  BEGIN, INSERT risposta, COMMIT
                              ...se qui il modello fallisce?
  rollback                 ->  non c'e' niente da disfare: la domanda e' gia' dentro
```

Il risultato è una domanda in database senza risposta, e un rollback che non può farci niente perché
la prima scrittura è già partita. Il danno peggiore è che **non è visibile subito**: la chat
funziona, finché un giorno un provider va in timeout.

Quindi:

- **repository**: `flush()` e `refresh()`. Non sanno cosa sia una transazione. Non hanno una sessione
  per la transaction, hanno una sessione e basta.
- **servizi**: ricevono `session` esplicitamente e sono gli unici a chiamare `commit()`.
- **in caso di errore**: `rollback()` esplicito, non affidato alla chiusura della sessione.

E il test che dimostra il punto:

```python
# tests/test_accounts.py
class ExplodingLLMProvider:
    async def complete(self, messages, max_tokens=500) -> NoReturn:
        raise TimeoutError("llm non risponde")


with pytest.raises(TimeoutError):
    await service.send_message("new", "Quanto ho speso a marzo?")

# prima e dopo, la differenza deve essere zero
```

`NoReturn` e non `object` nel tipo di ritorno: la funzione non torna mai, e dichiararlo evita di dover
inventare una risposta fittizia solo per soddisfare il protocollo.

Un dettaglio che il test mi ha insegnato: **non si contano le righe assolute**. La tabella delle chat
è condivisa dagli altri test e non viene mai svuotata, quindi `assert sessions == 0` fallirebbe per
righe scritte da qualcun altro. Quello che interessa è la differenza prima/dopo, che è ciò che questa
richiesta ha scritto.

E un buco che questo modo di ragionare ha fatto trovare: il `try` copriva le due scritture del turno,
ma non `_resolve_session` né il controllo di budget. Una richiesta con `session_id=new` e budget già
esaurito creava la riga di sessione, la metteva in flush, e poi il `429` la lasciava appesa alla
transazione. Via HTTP non si vedeva, perché `get_db` ripuliva; passato dal servizio con la sessione
data a mano, sì. Ora i due stanno dentro il `try`.

Lo stesso test verifica che il modello **non sia mai stato chiamato**: se il tetto si controllasse dopo
la richiesta all'LLM, la chiamata sarebbe stata pagata per poi rispondere `429`. Controllare che una
cosa *non* sia successa costa quanto controllare che sia successa, e spesso dice di più.

---

## 5. L'estensione dichiarata: una transazione attraversa due repository

È il punto che la consegna chiede di scrivere, e la scelta che l'ha reso non ovvia è **l'ordine delle
scritture**.

```python
# src/services/movements_service.py
await self._accounts.apply_delta(account_id, amount)  # UPDATE: parte SUBITO verso Postgres
movement = await self._movements.add(...)  # INSERT: puo' essere rifiutato
await self._movements.refresh(movement)
await self.session.commit()
```

L'`UPDATE` del saldo viene **prima**, e non per aestheticità: parte subito verso il database, ma non
è committato. Se l'`INSERT` viene rifiutato, l'`UPDATE` è in transazione e il rollback lo cancella
davvero. Con l'ordine inverso (`INSERT` prima, `UPDATE` dopo) il rollback non avrebbe niente da
disfare: la prima scrittura non sarebbe mai partita, e in memoria il rollback si limiterebbe a
scartare un attributo. Il test passerebbe comunque, ma non avrebbe provato niente.

E il rifiuto di cui parlo è reale, non ipotetico: `ck_movements_amount_not_zero`. Il test lo provoca
apposta con `model_construct`, che salta Pydantic di proposito, perché l'endpoint già rifiuta lo zero
con un `422` e qui si vuole provare il livello sotto.

**La parte che rende il test onesto**: la verifica passa da **un'altra sessione**, quindi da un'altra
connessione.

```python
async with factory() as check:  # sessione nuova
    stored = await check.get(Account, account.id)
    assert stored.balance == Decimal("60.00")
```

Rileggere dalla stessa sessione che aveva scritto non avrebbe provato niente: la transazione annullata
e il valore mai scritto si vedono identi. Leggere da un'altra connessione è la differenza fra "non ha
mai scritto" e "ha scritto e poi ha annullato".

---

## 6. Le due query del giorno

### Query 1: il JOIN, e come si dimostra che non è un N+1

Il primo istinto per "dammi i movimenti di un conto con l'intestatario" è `list` + `get` per ogni riga.
Funziona, e a 10 righe non si vede.

```python
stmt = (
    select(Movement, Account.holder, Account.iban)
    .join(Account, Movement.account_id == Account.id)
    .where(Movement.account_id == account_id)
)
```

Ma un N+1 non si vede nei risultati, si vede nel **conteggio**. Per questo nel `conftest` c'è un
`QueryRecorder` che ascolta `before_cursor_execute` e conta le frasi che arrivano davvero al driver:

```python
for how_many in (4, 40):
    ... query_recorder.start(); list_movements(...); query_recorder.stop()
    counts.append(query_recorder.count)

assert counts[0] == counts[1]        # non cresce con le righe
assert counts[0] > 0                 # il contatore ha visto qualcosa
assert any("JOIN" in s for s in ...) # ed era davvero un JOIN
```

Le ultime due righe sono la parte che rende la prova onesta. Senza `counts[0] > 0`, un contatore
rotto che non registra niente darebbe `0 == 0` e il test passerebbe: una prova che misura il nulla
dimostra il nulla. E senza il controllo sul testo, due query diverse con lo stesso numero di frasi
passerebbero entrambe: si controlla *quale* query è passata, non solo quante.

### Query 2 e 3: l'aggregazione, e cosa sia davvero `total_out`

```sql
to_char(occurred_on, 'YYYY-MM') AS month,
       COUNT(*),  SUM(-amount) FILTER (WHERE amount < 0)
```

`FILTER (WHERE amount < 0)` c'è per una ragione precisa: **le spese sono numeri negativi**, e
`SUM(amount)` su un trimestre in cui entrate e uscite si compensano non è "quanto ho speso" ma
"quanto è cambiato il conto". Due domande diverse, due numeri diversi.

Per questo la risposta tiene separati `total_in`, `total_out` e `net`:

- `total_out` somma le sole uscite e restituisce un **positivo**;
- `net` è la differenza con segno, e riguarda il conto intero.

È una scelta di comfort per chi legge: obbligare a ricordare che "le spese sono negative" è una
piccola trappola che si può evitare.

Le righe del `GROUP BY` crescono coi **mesi**, non coi movimenti. Il test lo dice con i numeri: otto
movimenti su tre mesi danno tre righe, e `total_out` è la somma dei tre bucket.

---

## 7. Il seed: una riga di codice che era un bug travestito

La prima versione del seed aveva gli offset con il segno sbagliato:

```python
occurred_on = date_to - timedelta(days=-63)  # sessantatre giorni *dopo* la fine
```

Tutti i movimenti finivano fuori finestra, il filtro li scartava, e il comando usciva con **tre conti
vuoti e un messaggio di successo**. Nessun errore, nessun traceback, un database con dentro solo
intestazioni.

Un filtro che butta via il 100% di quello che riceve non è un filtro: è un bug travestito da caso
normale. Adesso il seed conta quello che scarta e **solleva**, prima del `commit`:

```python
if dropped:
    raise ValueError(f"{len(dropped)} movimenti fuori dalla finestra ...")
```

Tre righe che ho scritto volentieri, perché il costo di quelle mancanti l'ho pagato.

Altre due cose del seed che ho imparato a scrivere:

- **Il saldo non si calcola in Python riga per riga.** Si ricalcola con la stessa `SUM` che usa
  `reconcile`. Due implementazioni dello stesso calcolo possono dare risultati diversi, e quella
  sbagliata è quella che nessuno controlla a mano. Così il seed non può essere la fonte di uno
  sbilancio, e se i due metodi dicessero cose diverse la riconciliazione lo direbbe subito.
- **Un solo `commit`.** Il seed è un caricamento in blocco, non una richiesta: passare da
  `MovementService` farebbe 42 transazioni dove ne serve una.

E i numeri del docstring sono verificati da un test, perché i docstring non si eseguono:

```python
# test_il_seed_scrive_tre_conti_e_quarantadue_movimenti
assert written == 42
```

Il testo diceva "quaranta" e il codice ne scriveva 42. Nessuno dei due numeri era verificato, quindi
erano entrambi sbagliati in un modo diverso. Ora il test esegue la promessa.

---

## 8. La migration: cosa non va fatto con l'autogenerate

`alembic revision --autogenerate` ha proposto, oltre a `accounts` e `movements`:

```python
op.alter_column("document_chunks", "chunk_metadata", ...)  # NULL -> NOT NULL
op.drop_index("ix_document_chunks_embedding", ...)  # <- l'indice HNSW del retrieval
```

Nessuno dei due toccava le tabelle del giorno 3. Erano il confronto fra il database e i modelli su
tabelle che il giorno 3 non tocca, e il secondo avrebbe cancellato l'indice di vettori su cui si
regge il retrieval del giorno 1.

Li ho rimossi a mano. Una migration che cancella l'indice di una funzionalità per un motivo che non
ha a che fare con la giornata è un costo che nessuno saprebbe spiegare fra sei mesi.

**La lezione operativa**: l'autogenerate **propone, non decide**, e un `drop_index` in una migration
va letto come un avviso, non come un dettaglio di formattazione.

Verifiche fatte sulla migration, non solo scritta:

- doppio `alembic upgrade head` senza effetti (idempotente);
- `downgrade -1` seguito da `upgrade`;
- l'intera catena su un database vuoto, per essere sicuri che non dipendesse da dati preesistenti;
- l'indice HNSW confermato presente dopo l'upgrade.

---

## 9. `get_db` e il rollback che non è una riga

`get_db` fa rollback anche quando viene tutto bene? No, solo in caso di errore, e in modo esplicito:

```python
async with async_session_factory() as session:
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
```

È in parte ridondante: chiudere una sessione con una transazione aperta la annulla già. L'ho lasciato
perché è una riga che **dice** cosa succede quando una richiesta muore a metà, e perché la lezione
del giorno è che quella riga da sola non basta più se il `commit` torna in un repository. Il
commento nel codice lo dice: da sola non ti salva, ti salva finché i servizi sono l'unico posto che
committa.

---

## 10. Una cosa che ho lasciato stare, e perché

`cost_eur` è un `float`, anche se tutto il resto del denaro è `Decimal`. L'ho lasciato, e l'argomento
è in `docs/ai-review/G3.md` punto 16: **non è denaro contabile**, è una stima del costo di una
chiamata a un modello. Non entra mai in un saldo e non viene sommato con un `amount`.

La decisione che va presa quando si prenderà non è "float o Decimal" ma "il costo del modello è denaro
o telemetria". Se è fatturazione diventa `NUMERIC` come `amount`; se è telemetria resta `float` e fuori
dai contratti di denaro. Il confine è dichiarato, ed è il motivo per cui la giornata vale anche senza
quel fix.

---

## 11. Verifiche eseguite

```bash
uv run ruff check .                                    ->  All checks passed
uv run mypy src tests scripts alembic/env.py           ->  no issues, 72 file
uv run pytest                                          ->  107 passed, 2 deselected
uv run python scripts/day3_smoke.py                     ->  9 verifiche conformi
```

Le prime otto verifiche dello smoke girano dentro un server avviato dallo script e lo interrogano via
HTTP. La nona è diversa per costruzione: gira in un **processo Python separato**, lanciato come
subprocess dopo che il primo è stato chiuso.

```python
completato = subprocess.run([sys.executable, script, "--dopo-riavvio", session_id, account_id], ...)
```

Un Python nuovo, un engine nuovo, nessuna memoria condivisa. Se la chat e il saldo ci sono anche lì,
è PostgreSQL a ricordarli, non il processo. È l'unica prova che distingue "ho salvato" da "avevo
ancora in memoria", e i test automatici non la fanno bene perché condividono il processo col
proprietario dei dati.

Unico trucco dichiarato: per non chiamare un'API a pagamento il provider LLM è sostituito con uno stub
via `app.dependency_overrides`. HTTP vero, sessione vera, transazione vera; lo stub cambia chi risponde,
non cosa viene scritto.

Due bug veri incontrati scrivendo proprio quello script, entrambi della stessa natura:

- **`Event loop is closed`.** `asyncio.run` chiude il loop quando il seed finisce, ma l'engine è
  creato a livello di modulo e nel suo pool ci sono connessioni aperte da quel loop. Il server che
  parte dopo gira in un loop diverso e riuserebbe connessioni morte: ogni richiesta finiva in `500` con
  un errore che non somigliava per niente alla causa. Si risolve con `await engine.dispose()` dopo il
  seed.
- Il contatore di query che misurava il nulla passava senza accorgersene. Vedi §6.

E una cosa sul modo di scrivere i test, che mi è costata una verifica inutile. Dopo aver scritto il
test sul budget ho voluto essere sicuro che fallisse **senza** la correzione, perché un test che passa
sempre non dimostra niente. Ho provato a togliere il fix con una sostituzione di testo: il test è
passato lo stesso, e il motivo era che la sostituzione **non era stata applicata**. Il test non stava
dimostrando che il fix serve, stava dimostrando che la mia sostituzione non faceva niente. Rifatta
con l'editor, il test è fallito come doveva (`assert (49, 128) == (48, 128)`, una sessione di troppo),
e solo dopo ho rimesso il fix.

La lezione è quella del contatore, applicata a me stesso: **un test che passa non dimostra che la
proprietà sia vera, dimostra solo che la misura non è rotta.** Le due cose si confondono facilmente,
e controllare che il test fallisca quando la proprietà è falsa è l'unico modo di sapere quale delle
due hai davanti.

---

## 12. Cose da rifare da soli (verifica di comprensione)

1. Perché `NUMERIC(14,2)` e non `REAL`? Cosa cambia a `2^53`?
2. Perché gli importi in JSON sono stringhe e non numeri? Fai il giro completo
   `Decimal → JSON → float → Decimal` con `97.50` e guarda cosa esce.
3. Perché l'`UPDATE` del saldo viene **prima** dell'`INSERT` del movimento? Cosa dimostrerebbe un
   test scritto con l'ordine inverso?
4. Perché la verifica dell'atomicità deve passare da un'altra sessione?
5. `balance` è la verità o la copia? Scrivi la frase che tiene insieme le due.
6. Nel seed, perché il filtro fuori finestra deve **sollevare** e non basta scartare in silenzio?
7. Cosa cambierebbe se il `commit` tornasse dentro `ChatRepository.add_message`?
8. Perché `count_user_sessions` con `func.count()` e non caricando le sessioni?
9. Nell'aggregazione, perché `SUM(amount)` non è "quanto ho speso"?
10. Cosa fa `engine.dispose()` e perché serve dopo un `asyncio.run`?
11. Perché `_resolve_session` e `check_budget` stanno dentro il `try`, e cosa controlla il test che
    verifica che il modello non sia stato chiamato?
12. Un test che passa dimostra che la proprietà è vera o solo che la misura non è rotta? Come si fa a
    saperlo?

---

## 13. Indice dei file toccati

| File | Cosa contiene |
| --- | --- |
| `src/db/models.py` | `Account`, `Movement`, `NUMERIC(14,2)`, `CHECK`, indice `(account_id, occurred_on)` |
| `src/db/repos.py` | `AccountRepository`, `MovementRepository`, JOIN, `GROUP BY`, `as_decimal()`, nessun `commit` |
| `src/db/session.py` | `get_db` con `rollback` esplicito |
| `src/db/seed.py` | tre conti, 42 movimenti, un `commit`, controllo che nulla venga scartato |
| `src/services/movements_service.py` | `record()` con saldo e movimento in una transazione, `reconcile()` |
| `src/services/chat_service.py` | una sola transazione per turno, anche nello streaming |
| `src/api/accounts.py` | le cinque rotte, nessuna `select()` |
| `src/types/accounts.py` | contratti Pydantic, denaro come stringa, validatore `amount != 0` |
| `src/exceptions.py` | `AccountNotFoundError` |
| `alembic/versions/b1b1de821ff3_add_accounts_and_movements.py` | la tabella, i vincoli, e i due `drop` tolti a mano |
| `tests/conftest.py` | database per test, `QueryRecorder` |
| `tests/test_accounts.py` | le query, il centesimo, l'atomicità, l'N+1, la chat, la riconciliazione, l'HTTP |
| `tests/test_chat.py` | il test del `429` aggiornato al contratto del giorno 2 |
| `scripts/day3_smoke.py` | le nove verifiche, l'ultima in un processo separato |

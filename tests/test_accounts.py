"""Il Giorno 3 in test: due query, un'estensione, e le prove che non devono fallire.

L'ordine dei test segue l'ordine della lezione: prima le due query, poi
l'estensione dichiarata, poi le tre cose che non devono succedere.
"""

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from typing import NoReturn
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import Account, ChatMessage, ChatSession, Movement
from src.db.repos import ChatRepository
from src.db.seed import seed
from src.exceptions import RateLimitError
from src.llm.client import Message, StreamChunk
from src.main import app
from src.observability.cost_tracker import CostTracker
from src.services.chat_service import ChatService
from src.services.movements_service import MovementService
from src.types.accounts import MovementCreateRequest
from tests.conftest import DbHandle, QueryRecorder
from tests.fakes import FakeLLMProvider


class ExplodingLLMProvider:
    """Provider che va sempre in errore, per provare cosa resta nel database.

    Il tipo di ritorno e' `NoReturn` e non `object`: la funzione non torna mai, e
    dichiararlo evita di dover inventare una risposta fittizia solo per soddisfare
    il protocollo. E' anche il modo piu' economico per far salire un'eccezione
        attraverso un'interfaccia che pretende un valore di ritorno.
    """

    model = "exploding"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages: list[Message], max_tokens: int = 500) -> NoReturn:
        self.calls += 1
        raise TimeoutError("llm non risponde")

    async def complete_stream(
        self, messages: list[Message], max_tokens: int = 500
    ) -> AsyncIterator[StreamChunk]:
        self.calls += 1
        yield StreamChunk(text="un pezzo")
        raise TimeoutError("llm caduta a meta' stream")


async def make_account(
    session: AsyncSession,
    *,
    holder: str = "Cliente Test",
    opening: Decimal = Decimal("0.00"),
) -> Account:
    """Apre un conto che rispetta l'invariante `balance == SUM(movements)`.

    Il conto parte a 0.00 e l'importo di apertura entra come movimento, passando
    dal servizio. E' il modo in cui un conto nasce in produzione, ed e' l'unico
    per cui `reconcile` ha senso: un conto creato con `balance=100.00` e senza
    cento euro in tabella mente gia' prima che arrivi qualcun altro.
    """
    account = Account(
        holder=holder,
        iban=f"IT02X054{uuid4().hex[:15]}",
        currency="EUR",
        opened_on=date(2020, 1, 1),
        balance=Decimal("0.00"),
    )
    session.add(account)
    await session.commit()

    if opening != 0:
        await MovementService(session).record(
            account.id,
            MovementCreateRequest(
                occurred_on=date(2020, 1, 2),
                description="Apertura conto",
                amount=opening,
            ),
        )
    return account


async def add_movements(
    session: AsyncSession,
    account: Account,
    amounts: list[str],
    *,
    days: list[date] | None = None,
    first_day: date = date(2026, 4, 1),
) -> None:
    occurred = days or [first_day + timedelta(days=i) for i in range(len(amounts))]
    session.add_all(
        [
            Movement(
                account_id=account.id,
                occurred_on=occurred[i],
                description=f"movimento {i}",
                amount=Decimal(value),
                currency="EUR",
            )
            for i, value in enumerate(amounts)
        ]
    )
    await session.commit()


async def movement_count(session: AsyncSession, account_id: UUID) -> int:
    stmt = select(func.count()).select_from(Movement).where(Movement.account_id == account_id)
    return int((await session.execute(stmt)).scalar_one())


# --------------------------------------------------------------------------
# Il seed: i numeri della documentazione devono corrispondere ai numeri scritti
# --------------------------------------------------------------------------


async def test_il_seed_scrive_tre_conti_e_quarantadue_movimenti(
    session: AsyncSession,
) -> None:
    """Il seed e' quello annunciato: tre conti, quarantadue movimenti, in un commit.

    Il docstring di `src/db/seed.py` promette "tre conti e quarantadue
    movimenti" e i docstring non si eseguono. Questo test esegue la promessa, cosi'
    se qualcuno aggiunge una riga a una lista e dimentica di aggiornare il testo,
    fallisce qui invece di lasciare due versioni diverse della stessa verita'.

    Verifica anche la riconciliazione su tutti e tre i conti: e' il controllo che
    dice che il percorso del seed e quello del servizio arrivano allo stesso
    numero, anche se i due non passano dallo stesso codice.
    """
    today = date(2026, 9, 28)
    written = await seed(session, today, reset_all=True)

    assert written == 42

    accounts = (await session.execute(select(Account).order_by(Account.iban))).scalars().all()
    assert len(accounts) == 3
    # L'ordinamento e' per IBAN, non per nome: lo si dichiara esplicitamente
    # invece di fidarsi dell'ordine di inserimento, che qui coincide.
    assert [a.holder for a in accounts] == ["Giulia Ferrara", "Marco Rinaldi", "Aisha Conte"]
    assert [await movement_count(session, a.id) for a in accounts] == [21, 11, 10]

    for account in accounts:
        stored, recomputed = await MovementService(session).reconcile(account.id)
        assert stored == recomputed, f"{account.holder}: saldo e somma divergono"

    # Rilanciarlo non raddoppia niente: e' quello su cui si fonda il poter
    # ricaricare il database in sviluppo senza ricominciare da zero.
    assert await seed(session, today, reset_all=True) == 42
    assert int((await session.execute(select(func.count()).select_from(Account))).scalar_one()) == 3
    assert (
        int((await session.execute(select(func.count()).select_from(Movement))).scalar_one()) == 42
    )


# --------------------------------------------------------------------------
# Le due query del giorno
# --------------------------------------------------------------------------


async def test_join_porta_intestatario_e_movimenti_in_un_giro(
    session: AsyncSession, query_recorder: QueryRecorder
) -> None:
    """Query 1: JOIN. L'intestatario non costa una query per movimento."""
    account = await make_account(session, holder="Giulia Ferrara")
    await add_movements(session, account, ["-10.00", "-25.50", "100.00"])

    query_recorder.start()
    result = await MovementService(session).list_movements(account.id)
    query_recorder.stop()

    assert result.holder == "Giulia Ferrara"
    assert result.iban == account.iban
    assert result.count == 3
    assert query_recorder.touching("movements") == 1, (
        "i movimenti si leggono con una sola query, non una per riga"
    )


async def test_il_totale_torna_al_centesimo(session: AsyncSession) -> None:
    """Query 2: la somma arriva esatta, e arriva come `Decimal`.

    Le sette commissioni da 0.10 fanno -0.70. In `float` farebbero
    -0.7000000000000001. Qui si controlla il *tipo*, non il numero stampato,
    perche' `0.70` e `0.7000000000000001` stampati sembrano uguali e sono due
    numeri diversi: e' il caso in cui un test che guarda il valore passa senza
    aver verificato niente.
    """
    account = await make_account(session)
    await add_movements(session, account, ["-0.10"] * 7)

    summary = await MovementService(session).summary(
        account.id, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)
    )

    assert summary.total_out == Decimal("0.70")
    assert summary.net == Decimal("-0.70")
    assert isinstance(summary.net, Decimal), "la somma deve tornare Decimal, non float"
    assert Decimal(str(sum(-0.10 for _ in range(7)))) != Decimal("-0.70"), (
        "se questo fallisce, il float non sta piu' sbagliando e il test non dimostra niente"
    )


async def test_aggregazione_per_mese_raggruppa_su_mesi(
    session: AsyncSession,
) -> None:
    """Query 3: GROUP BY. Le righe crescono coi mesi, non coi movimenti."""
    account = await make_account(session)
    await add_movements(
        session,
        account,
        ["-10.00", "-20.00", "-30.00", "-5.00", "-7.50", "-1.25", "-2.75", "-4.00"],
        days=[
            date(2026, 4, 20),
            date(2026, 4, 25),
            date(2026, 5, 1),
            date(2026, 5, 3),
            date(2026, 5, 30),
            date(2026, 6, 2),
            date(2026, 6, 15),
            date(2026, 6, 30),
        ],
    )

    summary = await MovementService(session).summary(
        account.id, date_from=date(2026, 4, 1), date_to=date(2026, 6, 30)
    )

    buckets = {b.month: b for b in summary.monthly}
    assert set(buckets) == {"2026-04", "2026-05", "2026-06"}
    # `total_out` e' l'uscita in positivo, la somma dei negativi col segono
    # ribaltato: un bucket e' "quando ho speso", non "dove finisce il conto".
    assert buckets["2026-04"].total_out == Decimal("30.00")
    assert buckets["2026-04"].movement_count == 2
    assert buckets["2026-05"].total_out == Decimal("42.50")
    assert buckets["2026-05"].movement_count == 3
    assert buckets["2026-06"].total_out == Decimal("8.00")
    assert buckets["2026-06"].movement_count == 3
    # Otto movimenti, tre righe fuori: la differenza e' la prova del GROUP BY.
    assert len(summary.monthly) == 3
    # Le tre parti sommano al totale. Qui `total_out` coincide con la somma dei
    # valori assoluti perche' tutti e otto i movimenti sono uscite: `total_out`
    # somma solo i negativi, gli entranti non lo toccano.
    assert summary.movement_count == 8
    assert summary.total_out == Decimal("80.50")


# --------------------------------------------------------------------------
# L'estensione dichiarata: una transazione attraversa due repository
# --------------------------------------------------------------------------


async def test_record_scrive_il_saldo_e_il_movimento_o_niente(
    db_engine_per_test: DbHandle,
) -> None:
    """L'unico test dell'estensione: saldo e movimento insieme, o nessuno dei due.

    Prima meta': la scrittura funziona davvero. Serve perche' un test che guarda
    solo il rollback passa ugualmente se il servizio non scrive niente.

    Seconda meta': il vincolo `ck_movements_amount_not_zero` fa rifiutare il
    movimento *dopo* che l'UPDATE del saldo e' gia' partito verso Postgres, e il
    rollback deve cancellare anche quello. La verifica passa da una sessione
    nuova, quindi da un'altra connessione: se il rollback avesse solo svuotato la
    sessione Python, questa leggerebbe comunque il saldo vecchio e il test
    passerebbe senza aver provato niente. Leggere da un'altra connessione e' la
    differenza fra "non ha mai scritto" e "ha scritto e poi ha annullato".
    """
    factory: async_sessionmaker[AsyncSession] = db_engine_per_test.session_factory

    async with factory() as setup:
        account = await make_account(setup, opening=Decimal("100.00"))
        # L'apertura del conto e' essa stessa un movimento, quindi il punto di
        # partenza non e' zero. I confronti sotto contano le *differenze*, cosi'
        # il test non deve sapere quanto ha scritto chi gli ha preparato il conto.
        movements_before = await movement_count(setup, account.id)

    # --- caso felice ---
    async with factory() as writer:
        view = await MovementService(writer).record(
            account.id,
            MovementCreateRequest(
                occurred_on=date(2026, 9, 1),
                description="Bonifico ricevuto",
                amount=Decimal("-40.00"),
            ),
        )
        assert view.balance == Decimal("60.00")

    async with factory() as check:
        stored = await check.get(Account, account.id)
        assert stored is not None
        assert stored.balance == Decimal("60.00"), "il caso felice non ha scritto il saldo"
        assert await movement_count(check, account.id) == movements_before + 1, (
            "il caso felice non ha scritto il movimento"
        )

    # --- caso rotto: la seconda scrittura viene rifiutata dal database ---
    async with factory() as writer:
        # `model_construct` salta la validazione Pydantic di proposito: l'endpoint
        # rifiuta gia' un importo a zero con un 422, e qui si vuole provare il
        # livello sotto, dove la richiesta arriva da un import batch o da un job
        # e nessuno e' passato da Pydantic.
        broken = MovementCreateRequest.model_construct(
            occurred_on=date(2026, 9, 2),
            description="Movimento rotto",
            amount=Decimal("0.00"),
            currency="EUR",
        )
        with pytest.raises(IntegrityError):
            await MovementService(writer).record(account.id, broken)

    # --- la prova, letta da un'altra connessione ---
    async with factory() as check:
        stored = await check.get(Account, account.id)
        assert stored is not None
        assert stored.balance == Decimal("60.00"), (
            "il movimento rifiutato ha modificato il saldo: la transazione non e' atomica"
        )
        assert await movement_count(check, account.id) == movements_before + 1, (
            "e' rimasto un movimento che non ha cambiato il saldo"
        )


# --------------------------------------------------------------------------
# Prova 1: le query non crescono col numero di righe
# --------------------------------------------------------------------------


async def test_il_numero_di_query_non_cresce_col_numero_di_movimenti(
    db_engine_per_test: DbHandle,
    query_recorder: QueryRecorder,
) -> None:
    """Quattro movimenti e quaranta: stesso numero di query, e un JOIN solo.

    Un N+1 non si vede nei risultati, si vede nel conteggio. Con `list` piu `get`
    per ogni riga le query sarebbero state 5 e 41; qui sono le stesse nei due casi.
    """
    factory: async_sessionmaker[AsyncSession] = db_engine_per_test.session_factory

    counts: list[int] = []
    for how_many in (4, 40):
        async with factory() as setup:
            account = await make_account(setup, holder=f"Cliente {how_many}")
            await add_movements(setup, account, [f"-{i + 1}.00" for i in range(how_many)])

        async with factory() as reader:
            query_recorder.start()
            result = await MovementService(reader).list_movements(account.id)
            query_recorder.stop()

        assert result.count == how_many, "il numero di righe restituite e' sbagliato"
        counts.append(query_recorder.count)

    assert counts[0] == counts[1], (
        f"le query crescono con le righe: {counts} invece di essere costanti"
    )
    # Senza questo, un contatore rotto che non registra niente darebbe 0 == 0 e
    # il passaggio: una prova che misura il nulla dimostra il nulla.
    assert counts[0] > 0, "il contatore non ha visto query: la prova non sta misurando niente"
    assert any("JOIN" in s.upper() for s in query_recorder.statements), (
        "movimenti e conto devono arrivare dallo stesso JOIN"
    )


# --------------------------------------------------------------------------
# Prova 2: la chat non lascia domande senza risposta
# --------------------------------------------------------------------------


async def test_un_turno_di_chat_rotto_non_lascia_la_domanda_nel_database(
    session: AsyncSession,
) -> None:
    """La ragione per cui il commit e' uscito dal repository.

    Il turno di chat scrive due messaggi: la domanda e la risposta. Con il commit
    dentro `add_message`, la domanda entrava al primo salvataggio e la risposta
    poteva non arrivare mai: in database restava una domanda senza risposta, e il
    rollback non aveva niente da disfare perche' la prima scrittura era gia'
    uscita dalla transazione. Adesso le due stanno nella stessa, e se la seconda
    fallisce non entra neanche la prima.
    """
    provider = ExplodingLLMProvider()
    service = ChatService(
        repo=ChatRepository(session),
        provider=provider,
        cost_tracker=CostTracker(session, 5.0),
        session=session,
    )

    # Contare le righe *assolute* qui non servirebbe a niente: la tabella delle
    # chat e' condivisa dagli altri test e non viene mai svuotata. Quello che
    # interessa e' la differenza prima/dopo, che e' cio' che questa richiesta ha
    # scritto. Se qualcuno reintroduce il commit nel repository, la differenza
    # smette di essere zero e il test lo dice.
    async def count_chat_rows() -> tuple[int, int]:
        sessions = int(
            (await session.execute(select(func.count()).select_from(ChatSession))).scalar_one()
        )
        messages = int(
            (await session.execute(select(func.count()).select_from(ChatMessage))).scalar_one()
        )
        return sessions, messages

    before = await count_chat_rows()

    with pytest.raises(TimeoutError):
        await service.send_message("new", "Quanto ho speso a marzo?")

    after = await count_chat_rows()
    assert provider.calls == 1
    assert after == before, (
        f"una richiesta fallita ha scritto {after[0] - before[0]} sessioni e "
        f"{after[1] - before[1]} messaggi: la domanda e' rimasta senza risposta"
    )


async def test_una_chat_rifiutata_per_budget_non_lascia_una_sessione_vuota(
    session: AsyncSession,
) -> None:
    """Il buco che il rollback copre anche quando non si arriva a scrivere i messaggi.

    `_resolve_session("new")` scrive e mette in flush la riga di sessione *prima* del
    controllo di budget. Se quel controllo solleva, la riga e' gia' in transazione
    ma nessun messaggio e' stato scritto: senza rollback, in database resta una
    conversazione vuota che non e' mai esistita per nessuno.

    Il caso passa dal servizio e non dall'endpoint perche' dall'endpoint il
    rollback lo fa `get_db` e il test passerebbe comunque. Qui la sessione e'
    data a mano: se il servizio non si annulla da solo, nessuno lo fa, ed e'
    esattamente la situazione che un test non deve nascondere.
    """
    # `max_eur_per_day=0.0` e' il caso limite che basta: la spesa della giornata e'
    # 0.0, il tetto e' 0.0, il confronto `>=` scatta e la richiesta viene respinta
    # senza che sia servito scrivere un centesimo fittizio in tabella.
    # Il provider che esplode serve anche a un secondo controllo: se il tetto fosse
    # verificato *dopo* la chiamata all'LLM, la richiesta verrebbe rifiutata ma
    # avremmo gia' pagato la chiamata. Con `calls == 0` si verifica anche l'ordine.
    provider = ExplodingLLMProvider()
    servizio = ChatService(
        repo=ChatRepository(session),
        provider=provider,
        cost_tracker=CostTracker(session, 0.0),
        session=session,
    )

    prima = (
        int((await session.execute(select(func.count()).select_from(ChatSession))).scalar_one()),
        int((await session.execute(select(func.count()).select_from(ChatMessage))).scalar_one()),
    )

    with pytest.raises(RateLimitError):
        await servizio.send_message("new", "Quanto ho speso?")

    dopo = (
        int((await session.execute(select(func.count()).select_from(ChatSession))).scalar_one()),
        int((await session.execute(select(func.count()).select_from(ChatMessage))).scalar_one()),
    )
    assert provider.calls == 0, "una richiesta già respinta è costata una chiamata al modello"
    assert dopo == prima, (
        f"una richiesta respinta per budget ha lasciato {dopo[0] - prima[0]} sessioni: "
        "una conversazione che non e' mai esistita"
    )


async def test_il_turno_di_chat_riuscito_scrive_una_ricorrenza_per_ruolo(
    fake_llm_provider: FakeLLMProvider,
) -> None:
    """Il caso felice della stessa cosa: due messaggi, una sola transazione.

    Passa dall'endpoint e non dal servizio, perche' qui non si sta provando la
    transazione (gia' fatto sopra) ma il percorso vero, dove la sessione arriva
    da `Depends(get_db)`.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/ai/chat", json={"session_id": "new", "message": "Ciao!"})

    assert response.status_code == 200
    session_id = UUID(response.json()["session_id"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        follow_up = await client.post(
            "/api/ai/chat", json={"session_id": str(session_id), "message": "E il saldo?"}
        )
    assert follow_up.status_code == 200

    # Il secondo turno ha trovato il primo in database: la sessione sopravvive
    # alla richiesta, quindi sopravvive anche al riavvio del processo.
    assert len(fake_llm_provider.calls) == 2
    assert "Ciao!" in str(fake_llm_provider.calls[1]), (
        "il secondo turno non ha trovato la storia del primo"
    )


# --------------------------------------------------------------------------
# Prova 3: la riconciliazione della copia denormalizzata
# --------------------------------------------------------------------------


async def test_reconcile_smette_una_scrittura_fatta_fuori_dal_servizio(
    session: AsyncSession,
) -> None:
    """Il saldo e' una copia: questa e' la domanda da porre a ogni scrittura.

    Le righe sono scritte a mano, senza passare dal servizio, quindi il saldo
    memorizzato resta quello iniziale: e' esattamente la situazione da cui
    `reconcile` deve accorgersi di una divergenza.
    """
    account = await make_account(session, opening=Decimal("0.00"))
    await add_movements(session, account, ["-10.10", "-0.10", "200.00", "-45.80"])

    stored, recomputed = await MovementService(session).reconcile(account.id)

    assert stored == Decimal("0.00")
    assert recomputed == Decimal("144.00")
    assert stored != recomputed, "il test deve mostrare una divergenza reale, non una finta"


async def test_record_aggiorna_il_saldo_e_la_riconciliazione_resta_vera(
    session: AsyncSession,
) -> None:
    """Dopo una scrittura normale la copia e la verta' continuano a combaciare."""
    account = await make_account(session, opening=Decimal("100.00"))

    await MovementService(session).record(
        account.id,
        MovementCreateRequest(
            occurred_on=date(2026, 9, 1),
            description="Uscita",
            amount=Decimal("-30.25"),
        ),
    )

    stored, recomputed = await MovementService(session).reconcile(account.id)
    # 100.00 di apertura, meno 30.25: se la copia includesse solo l'ultimo
    # movimento il risultato sarebbe -30.25, e questa e' la prova che la include
    # anche lo storico.
    assert stored == recomputed == Decimal("69.75")


# --------------------------------------------------------------------------
# Le rotte HTTP
# --------------------------------------------------------------------------


async def test_la_rotta_sintesi_risponde_come_il_modello(
    session: AsyncSession,
) -> None:
    """L'aggregazione passa dalla porta e gli importi viaggiano come stringhe."""
    account = await make_account(session, holder="Aisha Conte")
    await add_movements(session, account, ["-10.00", "-0.10", "50.00"], first_day=date(2026, 5, 1))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/api/ai/accounts/{account.id}/summary",
            params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["holder"] == "Aisha Conte"
    assert body["total_out"] == "10.10"
    assert body["net"] == "39.90"
    # Il denaro in JSON e' stringa, ed e' una scelta: un numero JSON non ha
    # decimali, e il passaggio a virgola mobile e' dove si perde il centesimo.
    assert isinstance(body["total_out"], str)
    assert body["monthly"] == [{"month": "2026-05", "movement_count": 3, "total_out": "10.10"}]


async def test_la_rotta_crea_un_movimento_e_risponde_con_il_saldo(
    session: AsyncSession,
) -> None:
    account = await make_account(session, opening=Decimal("100.00"))
    before = await movement_count(session, account.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/ai/accounts/{account.id}/movements",
            json={"occurred_on": "2026-09-01", "description": "Caffe", "amount": "-2.50"},
        )

    assert response.status_code == 201
    # `balance` arriva come stringa: in JSON, `97.50` rileggito diventa float e
    # il percorso Decimal->float->Decimal comincia a perdere i centesimi.
    assert response.json()["balance"] == "97.50"
    assert await movement_count(session, account.id) == before + 1


async def test_importo_a_zero_risponde_422_prima_del_database(session: AsyncSession) -> None:
    """Il controllo sul perimetro non e' quello che regge la transazione.

    Qui l'endpoint risponde 422 e non arriva niente al database: e' il comportamento
    giusto per un cliente. Ma non e' lo stesso della prova di atomicita', che passa
    dal servizio e lascia il vincolo a Postgres. Se qualcuno toglie il validatore,
    questo test resta verde e la garanzia del servizio no: sono due livelli.
    """
    account = await make_account(session, opening=Decimal("100.00"))
    before = await movement_count(session, account.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/ai/accounts/{account.id}/movements",
            json={"occurred_on": "2026-09-01", "description": "Niente", "amount": "0.00"},
        )

    assert response.status_code == 422
    assert await movement_count(session, account.id) == before, (
        "la richiesta rifiutata ha scritto qualcosa in database"
    )


async def test_conto_inesistente_risponde_404() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/ai/accounts/{uuid4()}/movements")

    assert response.status_code == 404
    assert response.json()["error"] == "ACCOUNT_NOT_FOUND"


# --------------------------------------------------------------------------
# La regola che tiene insieme tutto il giorno
# --------------------------------------------------------------------------


def test_nessun_repository_committa() -> None:
    """Il commit sta nei servizi. Se torna dentro un repository, il giorno e' da rifare.

    Non prova un comportamento: rende esplicita una regola di architettura che
    nessun errore segnalerebbe da solo. Un repository che committa continua a
    funzionare in tutti i test precedenti, e proprio per questo il difetto
    arriverebbe a produzione.
    """
    import inspect

    from src.db.repos import AccountRepository, ChatRepository, MovementRepository

    for repo in (ChatRepository, AccountRepository, MovementRepository):
        assert ".commit()" not in inspect.getsource(repo), (
            f"{repo.__name__} committa: il commit appartiene al servizio"
        )


async def test_il_servizio_riceve_una_sola_sessione_e_la_usa_per_il_commit() -> None:
    """Il servizio deve poter committare: riceve la sessione, non solo i repository."""
    import inspect

    for service in (ChatService, MovementService):
        params = inspect.signature(service.__init__).parameters
        assert "session" in params, (
            f"{service.__name__} non riceve la sessione: non puo' committare"
        )
        assert ".commit()" in inspect.getsource(service), (
            f"{service.__name__} non committa da nessuna parte"
        )

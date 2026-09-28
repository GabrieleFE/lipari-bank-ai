"""Accesso ai dati. Nessun repository committa.

`flush()` e non `commit()`: `flush()` manda le scritture al database dentro la
transazione aperta, senza chiuderla. Chi decide che il lavoro e' finito e' il
service, e lo decide una volta sola per unita' di lavoro. Il motivo e' scritto
in `MovementService.record`, dove la cosa conta davvero; qui basta la regola.

Con un `commit()` dentro il repository, ogni metodo che scrive diventa una
transazione a se' e non esiste piu' un punto in cui "questa richiesta e' finita":
se la terza scrittura di una richiesta fallisce, le prime due restano gia' fuori
dalla transazione. Il rollback esiste, ma non ha piu' niente da disfare.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Numeric, case, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import Account, ChatMessage, ChatSession, Movement


def as_decimal(value: object, *, what: str) -> Decimal:
    """Non fidarti del tipo che ti torna: controllalo.

    Il rischio vero di `func.sum()` su una colonna `NUMERIC` non e' che restituisca
    un intero rotondo, e' che lungo il tragitto SQLAlchemy/asyncpg il tipo si
    perda e arrivi un `float`. Allora `SUM(amount)` su 0.10 + 0.20 + 0.30 restituisce
    0.6000000000000001, il JSON dice 0.6, e il conto e' sbagliato di un centesimo
    senza che nessun errore sia mai comparso. Per questo qui il tipo non si
    converte: si verifica. Se un domani qualcosa torna `float`, questa funzione
    alza `TypeError` invece di riparare in silenzio il numero.
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"{what}: atteso Decimal, arrivato {type(value).__name__} ({value!r})")
    return value


class ChatRepository:
    """Accesso al DB isolato: la via API per leggere/scrivere conversazioni."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_session(self, user_id: UUID | None = None) -> ChatSession:
        session = ChatSession(id=uuid4(), user_id=user_id)
        self._session.add(session)
        await self._session.flush()
        await self._session.refresh(session)
        return session

    async def find_session(self, session_id: UUID) -> ChatSession | None:
        stmt = (
            select(ChatSession)
            .where(ChatSession.id == session_id)
            .options(selectinload(ChatSession.messages))
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def add_message(
        self,
        *,
        session_id: UUID,
        role: str,
        content: str,
        tokens: int = 0,
        cost_eur: float = 0.0,
        model_used: str = "dummy",
    ) -> ChatMessage:
        message = ChatMessage(
            id=uuid4(),
            session_id=session_id,
            role=role,
            content=content,
            tokens=tokens,
            cost_eur=cost_eur,
            model_used=model_used,
            created_at=datetime.now(UTC),
        )
        self._session.add(message)
        await self._session.flush()
        await self._session.refresh(message)
        return message

    async def list_messages(self, session_id: UUID) -> Sequence[ChatMessage]:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def count_user_sessions(self, user_id: UUID) -> int:
        # `len(...scalars().all())` portava in Python un UUID per ogni sessione
        # per contarle. Il contatore lo sa fare Postgres senza materializzare nulla.
        stmt = select(func.count()).select_from(ChatSession).where(ChatSession.user_id == user_id)
        return (await self._session.execute(stmt)).scalar_one()


class AccountRepository:
    """Letture e scritture sui conti."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, account_id: UUID) -> Account | None:
        return await self._session.get(Account, account_id)

    async def list_all(self) -> Sequence[Account]:
        stmt = select(Account).order_by(Account.holder)
        return (await self._session.execute(stmt)).scalars().all()

    async def get_by_iban(self, iban: str) -> Account | None:
        stmt = select(Account).where(Account.iban == iban)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def apply_delta(self, account_id: UUID, amount: Decimal) -> Decimal:
        """Sposta il saldo di `amount` e restituisce il saldo risultante.

        Scritto come UPDATE e non come `account.balance = account.balance + amount`
        sull'oggetto ORM per una ragione precisa: questa e' la *prima* delle due
        scritture di `MovementService.record`, e serve che l'UPDATE sia davvero
        partito verso Postgres prima che la seconda fallisca. Solo cosi' il test
        di atomicita' dimostra qualcosa di vero (vedi
        `test_record_scrive_il_saldo_e_il_movimento_o_niente`): se il fallback
        fosse un attributo Python, un errore dopo questa riga non avrebbe mai
        scritto niente e il test passerebbe senza provare niente.

        `synchronize_session="fetch"` tiene allineata l'identity map: se l'Account
        e' gia' caricato, il suo `balance` in Python deve diventare quello del
        database, non restare il valore di prima.
        """
        stmt = (
            update(Account)
            .where(Account.id == account_id)
            .values(balance=Account.balance + amount)
            .returning(Account.balance)
            .execution_options(synchronize_session="fetch")
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return as_decimal(result.scalar_one(), what="apply_delta.balance")


class MovementRepository:
    """Letture e scritture sui movimenti, con le due query del giorno."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        account_id: UUID,
        occurred_on: date,
        description: str,
        amount: Decimal,
        currency: str = "EUR",
    ) -> Movement:
        movement = Movement(
            id=uuid4(),
            account_id=account_id,
            occurred_on=occurred_on,
            description=description,
            amount=amount,
            currency=currency,
        )
        self._session.add(movement)
        await self._session.flush()
        return movement

    async def list_for_account(
        self,
        account_id: UUID,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 100,
    ) -> Sequence[tuple[Movement, str, str]]:
        """Query 1 del giorno: i movimenti del conto, con l'intestatario.

        Il JOIN e' una riga sola, non un `Account` letto a parte e un ciclo. E'
        quello che rende la risposta costante: due query fanno sempre lo stesso
        lavoro, sia con 10 righe che con 10.000. (Il percorso N+1 sarebbe stato
        `list_for_account_ids()` e poi un `get()` per ogni id: stesso risultato,
        1 + N query.)
        """
        stmt = (
            select(Movement, Account.holder, Account.iban)
            .join(Account, Movement.account_id == Account.id)
            .where(Movement.account_id == account_id)
            .order_by(Movement.occurred_on.desc(), Movement.created_at.desc())
            .limit(limit)
        )
        if date_from is not None:
            stmt = stmt.where(Movement.occurred_on >= date_from)
        if date_to is not None:
            stmt = stmt.where(Movement.occurred_on <= date_to)
        rows = (await self._session.execute(stmt)).all()
        return [(row[0], row[1], row[2]) for row in rows]

    async def total_for_account(self, account_id: UUID) -> Decimal:
        """La ricalcolata: la somma dei movimenti, ignorando il saldo memorizzato.

        Serve a `reconcile` per dimostrare che la copia non si e' staccata dalla
        verta'. Se `balance` e questa somma divergono, la copia ha mentito.
        """
        stmt = select(func.coalesce(func.sum(Movement.amount), 0)).where(
            Movement.account_id == account_id
        )
        value = (await self._session.execute(stmt)).scalar_one()
        return as_decimal(value, what="total_for_account")

    async def summary_for_period(
        self,
        account_id: UUID,
        *,
        date_from: date,
        date_to: date,
    ) -> tuple[int, Decimal, Decimal, Decimal]:
        """Query 2 del giorno: aggregazione per periodo. Una riga sola.

        Le somme sono `cast(... Numeric(14, 2))` per un motivo che la lezione
        chiama per nome: `func.sum()` su una colonna `NUMERIC` torna `Decimal`,
        ma se il tipo si perde lungo la strada SQLAlchemy/asyncpg puo' consegnare
        un `float`, e il centesimo che hai appena vinto in colonna lo perdi nel
        passaggio. Qui il tipo e' dichiarato due volte, e `test_il_totale_torna_al_centesimo`
        controlla che il valore sia davvero `Decimal` a runtime invece di fidarsi.
        """
        total_in = cast(
            func.coalesce(func.sum(case((Movement.amount > 0, Movement.amount), else_=0)), 0),
            Numeric(14, 2),
        )
        total_out = cast(
            func.coalesce(func.sum(case((Movement.amount < 0, -Movement.amount), else_=0)), 0),
            Numeric(14, 2),
        )
        net = cast(func.coalesce(func.sum(Movement.amount), 0), Numeric(14, 2))
        stmt = select(
            func.count().label("movement_count"),
            total_in.label("total_in"),
            total_out.label("total_out"),
            net.label("net"),
        ).where(
            Movement.account_id == account_id,
            Movement.occurred_on >= date_from,
            Movement.occurred_on <= date_to,
        )
        row = (await self._session.execute(stmt)).one()
        return (
            int(row.movement_count),
            as_decimal(row.total_in, what="total_in"),
            as_decimal(row.total_out, what="total_out"),
            as_decimal(row.net, what="net"),
        )

    async def monthly_totals(
        self,
        account_id: UUID,
        *,
        date_from: date,
        date_to: date,
    ) -> Sequence[tuple[str, int, Decimal]]:
        """Query 3 del giorno: la stessa aggregazione, ma raggruppata per mese.

        E' un GROUP BY. Il numero di righe che tornano cresce con i *mesi* del
        periodo, non con i movimenti: 40 righe in tre mesi e 4.000 righe in tre
        mesi danno la stessa risposta in quattro righe.
        """
        month = func.to_char(func.date_trunc("month", Movement.occurred_on), "YYYY-MM")
        spent = cast(
            func.sum(case((Movement.amount < 0, -Movement.amount), else_=0)),
            Numeric(14, 2),
        )
        stmt = (
            select(
                month.label("month"),
                func.count().label("movement_count"),
                spent.label("total_out"),
            )
            .where(
                Movement.account_id == account_id,
                Movement.occurred_on >= date_from,
                Movement.occurred_on <= date_to,
            )
            .group_by(month)
            .order_by(month)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            (str(row.month), int(row.movement_count), as_decimal(row.total_out, what="total_out"))
            for row in rows
        ]

    async def count_all(self) -> int:
        stmt = select(func.count()).select_from(Movement)
        return (await self._session.execute(stmt)).scalar_one()

    async def count_by_account(self, account_id: UUID) -> int:
        stmt = select(func.count()).select_from(Movement).where(Movement.account_id == account_id)
        return (await self._session.execute(stmt)).scalar_one()

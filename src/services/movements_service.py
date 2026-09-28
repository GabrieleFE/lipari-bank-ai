"""Il servizio dei movimenti: e' qui che si decide quando una richiesta e' finita.

Estensione scelta per il Giorno 3 (una delle quattro): **una transazione che
attraversa due repository**. `record()` scrive due cose che devono stare insieme:

    1. il saldo del conto  ->  `AccountRepository.apply_delta`
    2. il movimento        ->  `MovementRepository.add`

Se il saldo si aggiorna e il movimento no, il conto mente. Se il movimento si
scrive e il saldo no, il conto mente dall'altra parte. Le due scritture sono
gia' partite verso Postgres quando la seconda viene rifiutata, e l'unica cosa
che le tiene insieme e' che il `commit()` e' uno solo: qui in fondo, dopo
entrambe, dentro il service.

La strada scartata era lasciare il `commit()` dentro i repository, come faceva
`ChatRepository` fino al giorno prima. Con il commit nel repository ogni metodo
che scrive e' una transazione a se' e non esiste piu' un punto in cui "questa
richiesta e' finita": il rollback automatico continua a funzionare, ma non ha
piu' niente da disfare, perche' quello che doveva tornare indietro e' gia'
uscito. Per questo il rollback qui e' scritto a mano invece di essere affidato
alla chiusura della sessione: e' la stessa operazione di `commit`, e se la
dimentichi con un `commit` ti resta il rollback implicito, che funziona finche'
nessuno ci scrive dentro.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Account
from src.db.repos import AccountRepository, MovementRepository
from src.exceptions import AccountNotFoundError
from src.types.accounts import (
    AccountSummary,
    BalanceView,
    MonthlyBucket,
    MovementCreateRequest,
    MovementList,
    MovementView,
)


class MovementService:
    """Query e scritture sui conti. Nessun endpoint parla direttamente col DB.

    Riceve la sessione oltre ai due repository perche' la sessione e' l'unita' di
    lavoro: e' lei che si committa e si annulla. Se il service ricevesse solo i
    repository, il confine della transazione resterebbeSPARso in quattro file e
    nessuno, leggendo uno qualsiasi dei quattro, potrebbe dire dove finisce una
    richiesta.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._accounts = AccountRepository(session)
        self._movements = MovementRepository(session)

    async def list_accounts(self) -> Sequence[BalanceView]:
        accounts = await self._accounts.list_all()
        return [BalanceView.model_validate(account) for account in accounts]

    async def get_balance(self, account_id: UUID) -> BalanceView:
        account = await self._require_account(account_id)
        return BalanceView.model_validate(account)

    async def list_movements(
        self,
        account_id: UUID,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 100,
    ) -> MovementList:
        """Query 1 del giorno: JOIN. L'intestatario arriva con i movimenti."""
        rows = await self._movements.list_for_account(
            account_id, date_from=date_from, date_to=date_to, limit=limit
        )
        if rows:
            holder, iban = rows[0][1], rows[0][2]
        else:
            # Nessun movimento: il JOIN non ha niente su cui agganciare l'intestatario,
            # quindi lo si chiede. Il 404 e' qui dentro perche' "conto vuoto" e "conto
            # inesistente" devono restare due risposte diverse.
            account = await self._require_account(account_id)
            holder, iban = account.holder, account.iban
        return MovementList(
            account_id=account_id,
            holder=holder,
            iban=iban,
            count=len(rows),
            movements=[
                MovementView(
                    id=movement.id,
                    account_id=movement.account_id,
                    occurred_on=movement.occurred_on,
                    description=movement.description,
                    amount=movement.amount,
                    currency=movement.currency,
                )
                for movement, _holder, _iban in rows
            ],
        )

    async def summary(
        self,
        account_id: UUID,
        *,
        date_from: date,
        date_to: date,
    ) -> AccountSummary:
        """Query 2 e 3 del giorno: aggregazione sul periodo, e la stessa per mese."""
        account = await self._require_account(account_id)
        count, total_in, total_out, net = await self._movements.summary_for_period(
            account_id, date_from=date_from, date_to=date_to
        )
        monthly_rows = await self._movements.monthly_totals(
            account_id, date_from=date_from, date_to=date_to
        )
        return AccountSummary(
            account_id=account_id,
            holder=account.holder,
            iban=account.iban,
            currency=account.currency,
            date_from=date_from,
            date_to=date_to,
            movement_count=count,
            total_in=total_in,
            total_out=total_out,
            net=net,
            balance=account.balance,
            monthly=[
                MonthlyBucket(month=month, movement_count=month_count, total_out=spent)
                for month, month_count, spent in monthly_rows
            ],
        )

    async def record(self, account_id: UUID, request: MovementCreateRequest) -> BalanceView:
        """L'estensione: saldo e movimento nella stessa transazione.

        L'ordine e' voluto e non e' decorativo. Prima l'UPDATE del saldo, che
        parte subito verso il database, poi l'INSERT del movimento, che il
        database puo' rifiutare. Se l'INSERT fallisce, l'UPDATE e' gia' in
        transazione ma non committato, e il rollback lo cancella davvero dal
        database. Con l'ordine inverso il test di atomicita' non dimostrerebbe
        niente: la prima scrittura non sarebbe mai partita e il rollback
        avrebbe solo da cancellare un attributo in memoria.
        """
        account = await self._require_account(account_id)
        try:
            balance = await self._accounts.apply_delta(account.id, request.amount)
            await self._movements.add(
                account_id=account.id,
                occurred_on=request.occurred_on,
                description=request.description,
                amount=request.amount,
                currency=request.currency,
            )
        except Exception:
            await self._session.rollback()
            raise

        # Qui finisce l'unita' di lavoro: un solo commit, dopo entrambe le scritture.
        await self._session.commit()
        account.balance = balance
        return BalanceView.model_validate(account)

    async def reconcile(self, account_id: UUID) -> tuple[Decimal, Decimal]:
        """Il saldo memorizzato e la somma ricalcolata dai movimenti.

        Non un endpoint di debug: e' la domanda che va posta ogni volta che si
        introduce una copia denormalizzata. Se i due numeri non coincidono, la
        copia ha mentito, e va saputo prima che lo sportello la legga.
        """
        account = await self._require_account(account_id)
        recomputed = await self._movements.total_for_account(account_id)
        return account.balance, recomputed

    async def _require_account(self, account_id: UUID) -> Account:
        account = await self._accounts.get(account_id)
        if account is None:
            raise AccountNotFoundError(str(account_id))
        return account

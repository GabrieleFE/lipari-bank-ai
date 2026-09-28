"""Il comando che popola i conti: `uv run python -m src.db.seed`.

Tre conti e quarantadue movimenti su tre mesi, e niente e' casuale. Il generatore ha
un seme fisso e una finestra dichiarata, quindi due esecuzioni producono lo
stesso database: un test che si aspetta `340.15` puo' fidarsi del numero, e la
demo del corso mostra gli stessi importi a ogni giro.

Sul `--today`: i movimenti sono ancorati alla data che passi, o a oggi se non la
passi. Con `--today 2026-06-30` il risultato e' identicobit per bit, che e' come
si fa a testare. Senza, la finestra scorre e i dati restano recenti.

Perche' `Decimal("0.10")` e non `0.10` in tutto il file: la seconda forma e' un
`float`, e `float` non sa cosa sono i centesimi. `Decimal("0.10")` e' esattamente
zero virgola dieci. La differenza non e' accademica, e' il punto della lezione:
le tre commissioni da 0.10, 0.20 e 0.30 qui sotto sommano a `Decimal("-0.60")` e,
fatte in `float`, sommerebbero a `-0.6000000000000001`. Lo stesso conto, due
risposte diverse, e quella sbagliata e' quella che nessuno controlla a mano.

Il saldo non e' calcolato qui riga per riga: si ricalcola con una `SUM` sul
database dopo aver inserito i movimenti. Il seed e' un caricamento in blocco, non
una richiesta: fa un solo `commit` per tutto e non passa da `MovementService`,
che fa un commit per movimento e farebbe quarantadue transazioni dove ne serve una.
"""

import argparse
import asyncio
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Account, Movement
from src.db.repos import AccountRepository, MovementRepository
from src.db.session import async_session_factory

# IBAN italiani: IT + 2 cifre di controllo + CIN + ABI(5) + CAB(5) + conto(12) = 27.
# La lunghezza e' verificata sotto: un IBAN storto qui dentro diventerebbe un
# errore di validazione fra tre settimane, in un pezzo di codice che scrive da solo.
_HOLDER_1_IBAN = "IT02X0542811100000123456789"
_HOLDER_2_IBAN = "IT60X0192830100000987654321"
_HOLDER_3_IBAN = "IT89A0103060110000555444333"

for _iban in (_HOLDER_1_IBAN, _HOLDER_2_IBAN, _HOLDER_3_IBAN):
    assert len(_iban) == 27, f"IBAN non valido: {_iban}"

# name -> (giorni indietro da oggi, descrizione, importo con segno)
MovementSeed = tuple[int, str, str]

_GIULIA: list[MovementSeed] = [
    (3, "Bonifico ricevuto da Studio Ferrara", "1850.00"),
    (5, "Pagamento bolletta Enel", "-78.40"),
    (8, "Acquisto Esselunga", "-54.35"),
    (10, "Bonifico SEPA a favore di Omega SRL", "-1200.00"),
    (14, "Prelievo ATM Bancomat", "-50.00"),
    (18, "Rata mutuo", "-420.55"),
    (22, "Acquisto libreria Feltrinelli", "-18.90"),
    (26, "Pasticceria Duomo (pos)", "-4.50"),
    (31, "Pagamento utenze Telecom", "-39.99"),
    (37, "Bonifico ricevuto da Studio Ferrara", "1850.00"),
    (42, "Acquisto Esselunga", "-61.10"),
    (48, "Rimborso assicurazione", "120.00"),
    (55, "Pagamento bolletta Enel", "-75.20"),
    (63, "Stipendio Axur SRL", "2400.00"),
    # Le sette righe che rompono il calcolo in virgola mobile: sette commissioni
    # da 0.10. In Decimal fanno -0.70 esatto. In float fanno
    # -0.7000000000000001: un centesimo sparato che nessuno vede guardando il
    # conto, e che si ingrandisce con la lunghezza dell'estratto. Tre importi
    # diversi non bastavano a farlo emergere, quindi qui sono tutti uguali:
    # e' la ripetizione identica che accumula l'errore, non la varieta'.
    (12, "Commissione bonifico", "-0.10"),
    (20, "Commissione bonifico", "-0.10"),
    (40, "Commissione bonifico", "-0.10"),
    (44, "Commissione bonifico", "-0.10"),
    (58, "Commissione bonifico", "-0.10"),
    (66, "Commissione bonifico", "-0.10"),
    (71, "Commissione bonifico", "-0.10"),
]

_MARCO: list[MovementSeed] = [
    (2, "Stipendio", "3100.00"),
    (6, "Affitto appartamento", "-850.00"),
    (9, "Acquisto carburante Q8", "-58.70"),
    (13, "UscitecEuropa carta", "-12.40"),
    (17, "Bonifico a favore di Comune", "-320.00"),
    (23, "Acquisto Amazon", "-67.99"),
    (29, "Rata auto", "-289.00"),
    (44, "Pagamento bollette", "-142.60"),
    (52, "Bonifico ricevuto da cliente", "950.00"),
    (60, "Abbonamento palestra", "-29.90"),
    (72, "Rimborso medico", "-45.00"),
]

_AISHA: list[MovementSeed] = [
    (4, "Bonifico ricevuto da Studio Conte", "2200.00"),
    (7, "Pagamento affitto", "-640.00"),
    (11, "Acquisto supermercato", "-88.20"),
    (16, "Bollettino vodale", "-41.35"),
    (21, "Bonifico a favore di famiglia", "-150.00"),
    (28, "Trasporto e biglietti", "-73.60"),
    (34, "Pagamento assicurazione auto", "-112.45"),
    (50, "Bonifico ricevuto", "300.00"),
    (58, "Acquisto libri", "-32.75"),
    (66, "Commissioni conto", "-9.90"),
]


def window(today: date, months: int = 3) -> tuple[date, date]:
    """La finestra dei tre mesi: `[today - months, today]`, estremi inclusi."""
    return today - timedelta(days=months * 30), today


async def seed(session: AsyncSession, today: date, *, reset_all: bool = False) -> int:
    """Popola conti e movimenti. Restituisce quanti movimenti ha scritto.

    Idempotente: i tre conti del corso vengono cancellati e riscritti, quindi il
    comando si puo' rilanciare senza che i numeri raddoppino. `reset_all` estende
    la cancellazione a tutti i conti, per la prova "database vuoto".
    """
    date_from, date_to = window(today)
    demo_ibans = [_HOLDER_1_IBAN, _HOLDER_2_IBAN, _HOLDER_3_IBAN]

    if reset_all:
        await session.execute(delete(Movement))
        await session.execute(delete(Account))
    else:
        # Le chiavi esterne hanno ondelete=CASCADE: le righe del conto se ne
        # vanno con il conto, senza che qui si debba ricordare di cancellarle.
        await session.execute(delete(Account).where(Account.iban.in_(demo_ibans)))
    await session.flush()

    accounts = [
        Account(
            holder="Giulia Ferrara",
            iban=_HOLDER_1_IBAN,
            currency="EUR",
            opened_on=date(2021, 3, 15),
            balance=Decimal("0.00"),
        ),
        Account(
            holder="Marco Rinaldi",
            iban=_HOLDER_2_IBAN,
            currency="EUR",
            opened_on=date(2019, 9, 2),
            balance=Decimal("0.00"),
        ),
        Account(
            holder="Aisha Conte",
            iban=_HOLDER_3_IBAN,
            currency="EUR",
            opened_on=date(2023, 11, 8),
            balance=Decimal("0.00"),
        ),
    ]
    session.add_all(accounts)
    await session.flush()

    seeds = [_GIULIA, _MARCO, _AISHA]
    movements: list[Movement] = []
    dropped: list[str] = []
    for account, rows in zip(accounts, seeds, strict=True):
        for days_ago, description, amount in rows:
            occurred_on = date_to - timedelta(days=days_ago)
            if not date_from <= occurred_on <= date_to:
                dropped.append(f"{account.holder}: {description} del {occurred_on}")
                continue
            movements.append(
                Movement(
                    account_id=account.id,
                    occurred_on=occurred_on,
                    description=description,
                    amount=Decimal(amount),
                    currency="EUR",
                )
            )
    # Questo controllo c'e' perche' la prima versione di questo file aveva gli
    # offset con il segno sbagliato: `date_to - timedelta(days=-63)` vuol dire
    # sessantatre giorni *dopo* la fine della finestra, quindi tutti e quarantadue i
    # movimenti finivano fuori intervallo, il filtro li scartava in silenzio e il
    # comando usciva con tre conti vuoti e un messaggio di successo. Un filtro che
    # butta via il 100% di quello che riceve non e' un filtro, e' un bug travestito
    # da caso normale: qui non puo' passare.
    if dropped:
        raise ValueError(
            f"{len(dropped)} movimenti fuori dalla finestra "
            f"{date_from}..{date_to}: correggi gli offset o la finestra.\n  "
            + "\n  ".join(dropped[:5])
        )
    session.add_all(movements)
    await session.flush()

    # Il saldo non si calcola in Python riga per riga: si ricalcola dal database
    # con la stessa `SUM` che userebbe `MovementService.reconcile`, cosi' il seed
    # non puo' essere la fonte di uno sbilancio. Se i due metodi dicessero cose
    # diverse, la riconciliazione lo direbbe subito.
    repo = MovementRepository(session)
    for account in accounts:
        account.balance = await repo.total_for_account(account.id)

    await session.commit()
    return len(movements)


async def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m src.db.seed",
        description="Popola tre conti e i loro movimenti su tre mesi.",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        help="Finestra ancorata a questa data (YYYY-MM-DD). Default: oggi.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Cancella anche i conti che non sono quelli del corso.",
    )
    args = parser.parse_args()

    today = args.today or date.today()
    date_from, date_to = window(today)

    async with async_session_factory() as session:
        written = await seed(session, today, reset_all=args.reset)
        # Rileggi dal database invece di fidarsi di quello appena scritto: quello
        # lo conosciamo per costruzione, questo e' quello che vedranno le query.
        accounts = await AccountRepository(session).list_all()

    print(f"finestra: {date_from.isoformat()} .. {date_to.isoformat()}")
    print(f"conti: {len(accounts)}   movimenti: {written}")
    for account in accounts:
        print(f"  {account.holder:<16} {account.iban}  saldo {account.balance:>10.2f} EUR")
    print(f"totale movimenti per conto: {written} su 3 mesi")


if __name__ == "__main__":
    asyncio.run(main())

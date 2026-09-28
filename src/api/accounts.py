"""Le cinque rotte del Giorno 3: due query, una scrittura, la riconciliazione.

Nessuna di queste rotte costruisce una `select()`. Prendono una sessione, ci
mettono dentro un `MovementService` e gli chiedono il risultato. Il motivo e'
il criterio del giorno: se un endpoint sa scrivere SQL, allora il confine fra
"cosa il banco vuole" e "come si arriva ai dati" non esiste piu', e il giorno
dopo non c'e' piu' nessun posto dove mettere la transazione.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.services.movements_service import MovementService
from src.types.accounts import (
    AccountSummary,
    AccountSummaryList,
    BalanceView,
    MovementCreateRequest,
    MovementList,
)
from src.types.error import ErrorResponse

router = APIRouter(prefix="/api/ai/accounts", tags=["Accounts"])


def get_movement_service(session: AsyncSession = Depends(get_db)) -> MovementService:
    return MovementService(session)


@router.get(
    "",
    response_model=AccountSummaryList,
    summary="Elenco dei conti con il saldo",
    responses={404: {"model": ErrorResponse, "description": "Conto non trovato"}},
)
async def list_accounts(
    service: Annotated[MovementService, Depends(get_movement_service)],
) -> AccountSummaryList:
    return AccountSummaryList(accounts=list(await service.list_accounts()))


@router.get(
    "/{account_id}",
    response_model=BalanceView,
    summary="Il conto e il suo saldo",
    responses={404: {"model": ErrorResponse, "description": "Conto non trovato"}},
)
async def get_account(
    account_id: UUID,
    service: Annotated[MovementService, Depends(get_movement_service)],
) -> BalanceView:
    return await service.get_balance(account_id)


@router.get(
    "/{account_id}/movements",
    response_model=MovementList,
    summary="Movimenti del conto con l'intestatario (JOIN)",
    description=(
        "Movimenti del conto, con l'intestatario e l'IBAN. Intestatario e "
        "movimenti arrivano dallo stesso JOIN: due query in tutto, qualunque sia "
        "il numero di movimenti."
    ),
    responses={404: {"model": ErrorResponse, "description": "Conto non trovato"}},
)
async def list_movements(
    account_id: UUID,
    service: Annotated[MovementService, Depends(get_movement_service)],
    date_from: Annotated[date | None, Query(description="Data iniziale, inclusa")] = None,
    date_to: Annotated[date | None, Query(description="Data finale, inclusa")] = None,
    limit: Annotated[int, Query(ge=1, le=500, description="Movimenti massimi")] = 100,
) -> MovementList:
    return await service.list_movements(
        account_id, date_from=date_from, date_to=date_to, limit=limit
    )


@router.get(
    "/{account_id}/summary",
    response_model=AccountSummary,
    summary="Totali del periodo, per mese (aggregazione)",
    description=(
        "Totale entrato, totale uscito e saldo netto nel periodo, piu' lo stesso "
        "totale raggruppato per mese. Gli importi sono stringhe decimali "
        "esatte: un numero JSON non ha decimali e il passaggio a virgola mobile "
        "e' dove si perde il centesimo."
    ),
    responses={404: {"model": ErrorResponse, "description": "Conto non trovato"}},
)
async def account_summary(
    account_id: UUID,
    service: Annotated[MovementService, Depends(get_movement_service)],
    date_from: Annotated[date, Query(description="Data iniziale, inclusa")],
    date_to: Annotated[date, Query(description="Data finale, inclusa")],
) -> AccountSummary:
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return await service.summary(account_id, date_from=date_from, date_to=date_to)


@router.post(
    "/{account_id}/movements",
    response_model=BalanceView,
    status_code=201,
    summary="Registra un movimento e aggiorna il saldo",
    description=(
        "Scrive il movimento e sposta il saldo nella stessa transazione: o "
        "entrano entrambi, o non entra nessuno. Il saldo restituito e' quello "
        "dopo la scrittura."
    ),
    responses={
        201: {"model": BalanceView, "description": "Movimento registrato"},
        404: {"model": ErrorResponse, "description": "Conto non trovato"},
        422: {"model": ErrorResponse, "description": "Richiesta malformata"},
    },
)
async def create_movement(
    account_id: UUID,
    payload: MovementCreateRequest,
    service: Annotated[MovementService, Depends(get_movement_service)],
) -> BalanceView:
    return await service.record(account_id, payload)

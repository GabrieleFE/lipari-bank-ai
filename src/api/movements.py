from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, UploadFile
from fastapi import status as http_status

from src.exceptions import ImportFileError
from src.services.idempotency import InMemoryIdempotencyStore
from src.services.import_runner import MovementImportRunner
from src.services.movements_import_service import MAX_IMPORT_BYTES, MovementsImportService
from src.types.error import ErrorResponse
from src.types.movements import MovementImportResponse

router = APIRouter(prefix="/api/ai", tags=["Movements"])

CHUNK_BYTES = 64 * 1024

_store = InMemoryIdempotencyStore()


def get_idempotency_store() -> InMemoryIdempotencyStore:
    return _store


def get_movements_import_service() -> MovementsImportService:
    return MovementsImportService()


def get_import_runner(
    importer: Annotated[MovementsImportService, Depends(get_movements_import_service)],
    store: Annotated[InMemoryIdempotencyStore, Depends(get_idempotency_store)],
) -> MovementImportRunner:
    return MovementImportRunner(importer, store)


async def read_capped(file: UploadFile, limit: int = MAX_IMPORT_BYTES) -> bytes:
    """Legge il file a scaglie e si ferma appena supera il limite.

    `await file.read()` senza argomento porta in memoria tutto quello che arriva e il
    controllo di dimensione avviene dopo: un file da un gigabyte uccide il processo
    prima di arrivare al `413`. Qui il tetto e' applicato mentre si legge, quindi un
    file dieci volte piu' grande di quello provato costa un chunk, non un gigabyte.
    """
    buffer = bytearray()
    while chunk := await file.read(CHUNK_BYTES):
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise ImportFileError(
                "IMPORT_FILE_TOO_LARGE",
                f"Il file supera il limite di {limit} byte: segmentalo in piu' caricamenti",
                status_code=http_status.HTTP_413_CONTENT_TOO_LARGE,
            )
    return bytes(buffer)


@router.post(
    "/movements/import",
    response_model=MovementImportResponse,
    status_code=http_status.HTTP_200_OK,
    summary="Importa un estratto conto CSV e rende conto di ogni riga",
    description=(
        "Ogni riga viene validata con lo stesso contratto di /categorize. Le righe valide "
        "sono contate in imported_count, le righe scartate tornano in problems con il numero "
        "di riga nel file e il motivo. Nessuna riga sparisce dal conto e nessuna riga "
        "sbagliata passa per buona: imported_count + len(problems) == total_rows."
    ),
    responses={
        200: {"model": MovementImportResponse, "description": "Riepilogo dell'import"},
        400: {
            "model": ErrorResponse,
            "description": "File vuoto, non leggibile o intestazione errata",
        },
        409: {
            "model": ErrorResponse,
            "description": "Stessa Idempotency-Key con un file diverso",
        },
        413: {"model": ErrorResponse, "description": "File oltre il limite di 5 MB"},
        422: {"model": ErrorResponse, "description": "Richiesta malformata: manca il file"},
    },
)
async def import_movements(
    file: Annotated[
        UploadFile,
        File(description="CSV con intestazione date,description,amount,currency"),
    ],
    runner: Annotated[MovementImportRunner, Depends(get_import_runner)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description=(
                "Identificativo che il chiamante assegna al caricamento. Ricaricando la "
                "stessa chiave con lo stesso file il conto non si raddoppia."
            ),
        ),
    ] = None,
) -> MovementImportResponse:
    raw = await read_capped(file)
    return runner.run(raw, idempotency_key)

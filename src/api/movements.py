from fastapi import APIRouter, Depends, File, UploadFile, status

from src.services.movements_import_service import MovementsImportService
from src.types.error import ErrorResponse
from src.types.movements import MovementImportResponse

router = APIRouter(prefix="/api/ai", tags=["Movements"])


def get_movements_import_service() -> MovementsImportService:
    return MovementsImportService()


@router.post(
    "/movements/import",
    response_model=MovementImportResponse,
    status_code=status.HTTP_200_OK,
    summary="Importa un estratto conto CSV e rende conto di ogni riga",
    description=(
        "Ogni riga viene validata con lo stesso contratto di /categorize. Le righe valide sono "
        "contate in imported_count, le righe scartate tornano in problems con il numero di riga "
        "nel file e il motivo. Nessuna riga sparisce dal conto e nessuna riga sbagliata passa "
        "per buona."
    ),
    responses={
        200: {"model": MovementImportResponse, "description": "Riepilogo dell'import"},
        400: {
            "model": ErrorResponse,
            "description": "File vuoto, non leggibile o intestazione errata",
        },
        413: {"model": ErrorResponse, "description": "File oltre il limite di 5 MB"},
        422: {"model": ErrorResponse, "description": "Richiesta malformata: manca il file"},
    },
)
async def import_movements(
    file: UploadFile = File(
        ..., description="CSV con intestazione date,description,amount,currency"
    ),
    service: MovementsImportService = Depends(get_movements_import_service),
) -> MovementImportResponse:
    raw = await file.read()
    return service.import_csv(raw)

import csv
import io

from pydantic import ValidationError

from src.exceptions import ImportFileError
from src.types.error import ErrorDetail
from src.types.movements import ImportProblem, MovementImportResponse, MovementRow
from src.validation import describe_detail
from src.validation_bridge import details_from_validation_error

REQUIRED_COLUMNS = ("date", "description", "amount", "currency")
MAX_IMPORT_BYTES = 5_000_000
UNKNOWN_CODE = "INVALID_VALUE"


def _problem(row: int, detail: ErrorDetail) -> ImportProblem:
    return ImportProblem(row=row, reason=describe_detail(detail.model_dump()), detail=detail)


class MovementsImportService:
    """Valida un estratto conto riga per riga: nessuna riga sparisce, nessuna passa per buona.

    Non scrive nulla: il contratto dell'esercizio è il conteggio esatto e il resoconto
    delle righe rifiutate. La persistenza arriva con i repository, e per allora
    l'idempotenza (stesso file due volte) sara' un problema suo, non di questo metodo.

    La regola di validazione non e' qui dentro: sta in `MovementRow`. Questo servizio
    si limita a tradurre l'eccezione del modello nel formato che lo sportello legge.
    """

    def import_csv(self, raw: bytes) -> MovementImportResponse:
        if len(raw) > MAX_IMPORT_BYTES:
            raise ImportFileError(
                "IMPORT_FILE_TOO_LARGE",
                f"Il file supera il limite di {MAX_IMPORT_BYTES} byte: "
                "segmentalo in più caricamenti",
                status_code=413,
            )
        rows = self._read_rows(self._decode(raw))
        header, data_rows = self._split(rows)
        self._check_header(header)
        imported = 0
        problems: list[ImportProblem] = []
        for line_number, cells in data_rows:
            problem = self._validate_row(line_number, header, cells)
            if problem is None:
                imported += 1
            else:
                problems.append(problem)
        return MovementImportResponse(
            imported_count=imported,
            total_rows=len(data_rows),
            problems=problems,
        )

    def _decode(self, raw: bytes) -> str:
        if not raw.strip():
            raise ImportFileError(
                "EMPTY_IMPORT_FILE", "Il file è vuoto: manca l'intestazione del CSV"
            )
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ImportFileError(
                "IMPORT_FILE_NOT_READABLE",
                "Il file non è un CSV leggibile: atteso testo UTF-8 con separatore virgola",
            ) from exc

    def _read_rows(self, text: str) -> list[list[str]]:
        try:
            return list(csv.reader(io.StringIO(text, newline=""), strict=True))
        except csv.Error as exc:
            raise ImportFileError(
                "IMPORT_FILE_NOT_CSV",
                "Il file non è un CSV valido: separatore o citature malformate",
            ) from exc

    def _split(self, rows: list[list[str]]) -> tuple[list[str], list[tuple[int, list[str]]]]:
        kept = [
            (index + 1, row) for index, row in enumerate(rows) if any(cell.strip() for cell in row)
        ]
        if not kept:
            raise ImportFileError(
                "EMPTY_IMPORT_FILE", "Il file non contiene righe: manca l'intestazione del CSV"
            )
        return kept[0][1], kept[1:]

    def _check_header(self, header: list[str]) -> None:
        normalized = [cell.strip().lower() for cell in header]
        if normalized != list(REQUIRED_COLUMNS):
            raise ImportFileError(
                "INVALID_CSV_HEADER",
                "Intestazione attesa 'date,description,amount,currency', trovata "
                f"'{','.join(normalized) or '(vuota)'}'",
            )

    def _validate_row(
        self, line_number: int, header: list[str], cells: list[str]
    ) -> ImportProblem | None:
        if len(cells) != len(header):
            # Riga strutturalmente sbagliata: nessun campo del contratto c'e' da indicare,
            # quindi `field` resta a `row` e il codice dice 'quante colonne mancano/avanzano'.
            return _problem(
                line_number,
                ErrorDetail(
                    field="row",
                    code="COLUMN_COUNT_MISMATCH",
                    message=(f"ha {len(cells)} colonne, l'intestazione ne dichiara {len(header)}"),
                    expected=f"{len(header)} colonne",
                    received=str(len(cells)),
                ),
            )
        mapping = {name.strip().lower(): value for name, value in zip(header, cells, strict=True)}
        try:
            MovementRow.model_validate(mapping)
        except ValidationError as exc:
            return _problem(line_number, self._worst_detail(exc))
        return None

    def _worst_detail(self, exc: ValidationError) -> ErrorDetail:
        """Il problema più utile, non il primo.

        Pydantic restituisce gli errori nell'ordine dei campi del modello: se una riga
        ha una data inesistente e un importo a zero, riportare solo `date` lascia lo
        sportello a scoprire il resto al giro dopo. Si sceglie il primo errore che ha
        una spiegazione dedicata nel vocabolario, cosi' la riga viene sistemata una volta.
        """
        details = details_from_validation_error(exc)
        if not details:
            return ErrorDetail(field="row", code=UNKNOWN_CODE, message="riga non valida")
        return next((d for d in details if d.code != UNKNOWN_CODE), details[0])

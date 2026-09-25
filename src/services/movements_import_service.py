import csv
import io

from pydantic import ValidationError

from src.exceptions import ImportFileError
from src.types.movements import ImportProblem, MovementImportResponse, MovementRow

REQUIRED_COLUMNS = ("date", "description", "amount", "currency")
MAX_IMPORT_BYTES = 5_000_000
_MAX_ECHOED_VALUE = 50

_FIELD_HINTS: dict[str, str] = {
    "greater_than": "deve essere maggiore di zero",
    "string_too_short": "non può essere vuota",
    "string_too_long": "è troppo lunga (massimo 200 caratteri)",
    "string_pattern_mismatch": "deve essere un codice valuta di tre lettere maiuscole",
    "float_parsing": "deve essere un numero",
    "int_parsing": "deve essere un numero",
    "float_type": "deve essere un numero",
    "date_parsing": "non è una data valida in formato AAAA-MM-GG",
    "date_from_datetime_parsing": "non è una data valida in formato AAAA-MM-GG",
    "date_type": "non è una data valida in formato AAAA-MM-GG",
    "missing": "manca",
    "extra": "non è previsto dal contratto della riga",
}


def _echoed(value: object, limit: int = _MAX_ECHOED_VALUE) -> str:
    if value is None:
        return "assente"
    text = " ".join(str(value).split())
    if not text:
        return "vuoto"
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


class MovementsImportService:
    """Valida un estratto conto riga per riga: nessuna riga sparisce, nessuna passa per buona.

    Non scrive nulla: il contratto dell'esercizio è il conteggio esatto e il resoconto
    delle righe rifiutate. La persistenza arriva con i repository.
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
        return MovementImportResponse(imported_count=imported, problems=problems)

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
            return ImportProblem(
                row=line_number,
                field=None,
                reason=(f"riga: ha {len(cells)} colonne, l'intestazione ne dichiara {len(header)}"),
            )
        mapping = {name.strip().lower(): value for name, value in zip(header, cells, strict=True)}
        try:
            MovementRow.model_validate(mapping)
        except ValidationError as exc:
            field, reason = self._describe(exc)
            return ImportProblem(row=line_number, field=field, reason=reason)
        return None

    def _describe(self, error: ValidationError) -> tuple[str | None, str]:
        first_field: str | None = None
        parts: list[str] = []
        for item in error.errors(include_url=False):
            field = ".".join(str(part) for part in item["loc"])
            if first_field is None:
                first_field = field
            hint = _FIELD_HINTS.get(str(item["type"]), "non rispetta il formato atteso")
            parts.append(f"{field}: {hint} (valore ricevuto: {_echoed(item['input'])})")
        return first_field, "; ".join(parts)

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.services.movements_import_service import MAX_IMPORT_BYTES, MovementsImportService
from src.types.movements import MovementImportResponse

VALID_CSV = (
    "date,description,amount,currency\n"
    "2025-01-05,Bonifico Enel Energia,85.50,EUR\n"
    "2025-01-07,Esselunga supermercato,45.20,EUR\n"
    "2025-01-20,Coffee break,4.50,EUR\n"
)

MIXED_CSV = (
    "date,description,amount,currency\n"
    "2025-01-05,Bonifico Enel Energia,85.50,EUR\n"
    "2025-01-07,Esselunga supermercato,45.20,EUR\n"
    "2025-01-09,Distributore IP carburante,-60.00,EUR\n"
    "2025-01-11,Netflix abbonamento,0.00,EUR\n"
    "2025-01-13,,12.99,EUR\n"
    "2025-02-30,Penicillina,10.00,EUR\n"
    "2025-01-16,Pizzeria Da Michele,18.40,eur\n"
    "2025-01-18,Trenitalia biglietto,29.90\n"
    "2025-01-20,Coffee break,4.50,EUR\n"
)

ENVELOPE_KEYS = {"timestamp", "status", "error", "message", "path", "details"}


def upload(
    content: str | bytes, filename: str = "estratto.csv"
) -> dict[str, tuple[str, bytes, str]]:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return {"file": (filename, payload, "text/csv")}


async def post_import(
    content: str | bytes, filename: str = "estratto.csv"
) -> tuple[int, MovementImportResponse | None, dict[str, object]]:
    """Stato, risposta tipizzata (None se non e' un 200) e corpo grezzo.

    La risposta tipizzata si confronta campo per campo; il corpo grezzo serve per
    gli errori, dove il chiamante legge una busta e non un modello di successo.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/ai/movements/import", files=upload(content, filename))
    body: dict[str, object] = response.json()
    if response.status_code != 200:
        return response.status_code, None, body
    return response.status_code, MovementImportResponse.model_validate(body), body


async def test_import_accepts_every_valid_row() -> None:
    status_code, data, body = await post_import(VALID_CSV)
    assert status_code == 200
    assert data is not None
    assert body == {
        "imported_count": 3,
        "total_rows": 3,
        "problems": [],
        "replayed": False,
    }
    assert data.imported_count == 3


async def test_import_reports_every_rejected_row_with_its_number() -> None:
    status_code, data, _ = await post_import(MIXED_CSV)
    assert status_code == 200
    assert data is not None
    assert data.imported_count == 3
    problems = data.problems
    assert [problem.row for problem in problems] == [4, 5, 6, 7, 8, 9]
    campi = [problem.detail.field for problem in problems]
    assert campi == ["amount", "amount", "description", "date", "currency", "row"]
    codici = [problem.detail.code for problem in problems]
    assert codici == [
        "NOT_POSITIVE",
        "NOT_POSITIVE",
        "EMPTY",
        "NOT_A_DATE",
        "NOT_A_CURRENCY",
        "COLUMN_COUNT_MISMATCH",
    ]
    motivi = [problem.reason for problem in problems]
    assert "maggiore di zero" in motivi[0]
    # Il valore ricevuto e' la stringa del file, non il float che ne e' stato ricavato.
    assert "valore ricevuto: -60.00" in motivi[0]
    assert "vuoto" in motivi[2]
    assert "AAAA-MM-GG" in motivi[3]
    assert "tre lettere maiuscole" in motivi[4]
    assert "3 colonne" in motivi[5]


async def test_import_accounts_for_every_data_row() -> None:
    """La proprieta' che l'ufficio controlla: dentro + fuori = righe del file."""
    _, data, _ = await post_import(MIXED_CSV)
    assert data is not None
    data_rows = len([line for line in MIXED_CSV.splitlines()[1:] if line.strip()])
    assert data.total_rows == data_rows
    assert data.imported_count + len(data.problems) == data.total_rows == data_rows


async def test_header_only_file_imports_nothing_without_inventing_problems() -> None:
    status_code, data, body = await post_import("date,description,amount,currency\n")
    assert status_code == 200
    assert data is not None
    assert body == {
        "imported_count": 0,
        "total_rows": 0,
        "problems": [],
        "replayed": False,
    }


async def test_blank_lines_do_not_shift_row_numbers() -> None:
    csv_with_gap = (
        "date,description,amount,currency\n"
        "2025-01-05,Bonifico Enel,85.50,EUR\n"
        "\n"
        "2025-01-09,Cipolla,0.00,EUR\n"
    )
    _, data, _ = await post_import(csv_with_gap)
    assert data is not None
    # La riga 3 del file e' vuota ma la 4 e' la cipolla: il numero deve essere quello
    # del file, non quello delle righe non vuote.
    assert [problem.row for problem in data.problems] == [4]
    assert data.total_rows == 2


async def test_empty_file_is_a_domain_error_in_the_standard_envelope() -> None:
    status_code, _, body = await post_import("   \n")
    assert status_code == 400
    assert body["error"] == "EMPTY_IMPORT_FILE"
    assert set(body) == ENVELOPE_KEYS
    assert body["status"] == 400


async def test_file_that_is_not_a_csv_is_a_domain_error() -> None:
    status_code, _, body = await post_import("Questo e' un memo, non un estratto conto.\n")
    assert status_code == 400
    assert body["error"] == "INVALID_CSV_HEADER"
    assert set(body) == ENVELOPE_KEYS


async def test_malformed_quoting_is_a_domain_error_not_a_crash() -> None:
    broken = 'date,description,amount,currency\n2025-01-05,"Bonifico Enel,EUR\n'
    status_code, _, body = await post_import(broken)
    assert status_code == 400
    assert body["error"] == "IMPORT_FILE_NOT_CSV"
    assert set(body) == ENVELOPE_KEYS


async def test_missing_file_part_is_422_naming_the_field() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/ai/movements/import")
    assert response.status_code == 422
    body: dict[str, object] = response.json()
    assert set(body) == ENVELOPE_KEYS
    details = body["details"]
    assert isinstance(details, list)
    assert [d["field"] for d in details] == ["file"]
    assert details[0]["code"] == "MISSING"


async def test_error_bodies_never_leak_traceback_or_local_paths() -> None:
    for content in ("", "memo\n", 'a,b,c,d\n"x,y\n'):
        _, _, body = await post_import(content)
        text = str(body)
        assert "Traceback" not in text
        assert "movements_import_service" not in text
        assert "site-packages" not in text


def test_service_strips_whitespace_and_tolerates_a_bom() -> None:
    service = MovementsImportService()
    with_bom = "﻿date,description,amount,currency\n2025-01-05, Bonifico Enel ,85.50, EUR \n"
    result = service.import_csv(with_bom.encode("utf-8"))
    assert result.imported_count == 1
    assert result.problems == []


def test_service_rejects_a_file_over_the_size_limit() -> None:
    service = MovementsImportService()
    oversized = b"x" * (MAX_IMPORT_BYTES + 1)
    with pytest.raises(Exception) as error:
        service.import_csv(oversized)
    assert getattr(error.value, "status_code", None) == 413
    assert getattr(error.value, "code", None) == "IMPORT_FILE_TOO_LARGE"


def test_a_description_of_only_spaces_is_an_empty_description() -> None:
    """Senza `str_strip_whitespace` questa riga passerebbe: `min_length=1` e' soddisfatto.

    E' il caso che la consegna chiama 'importi che mancano, date che non esistono,
    valute scritte per esteso' nella sua forma piu' insidiosa: non e' un valore
    sbagliato, e' un valore che non c'e'.
    """
    result = MovementsImportService().import_csv(
        b"date,description,amount,currency\n2025-01-05,   ,10.00,EUR\n"
    )
    assert result.imported_count == 0
    assert result.problems[0].detail.code == "EMPTY"
    assert result.problems[0].detail.received == "vuoto"

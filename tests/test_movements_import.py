import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.services.movements_import_service import MAX_IMPORT_BYTES, MovementsImportService

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

ENVELOPE_KEYS = {"timestamp", "status", "error", "message", "path"}


def upload(
    content: str | bytes, filename: str = "estratto.csv"
) -> dict[str, tuple[str, bytes, str]]:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return {"file": (filename, payload, "text/csv")}


async def post_import(
    content: str | bytes, filename: str = "estratto.csv"
) -> tuple[int, dict[str, object]]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/ai/movements/import", files=upload(content, filename))
    body: dict[str, object] = response.json()
    return response.status_code, body


async def test_import_accepts_every_valid_row() -> None:
    status_code, body = await post_import(VALID_CSV)
    assert status_code == 200
    assert body == {"imported_count": 3, "problems": []}


async def test_import_reports_every_rejected_row_with_its_number() -> None:
    status_code, body = await post_import(MIXED_CSV)
    assert status_code == 200
    assert body["imported_count"] == 3
    problems = body["problems"]
    assert isinstance(problems, list)
    assert [problem["row"] for problem in problems] == [4, 5, 6, 7, 8, 9]
    fields = [problem["field"] for problem in problems]
    assert fields == ["amount", "amount", "description", "date", "currency", None]
    reasons = [problem["reason"] for problem in problems]
    assert "maggiore di zero" in reasons[0]
    assert "valore ricevuto: -60.0" in reasons[0]
    assert "vuoto" in reasons[2]
    assert "AAAA-MM-GG" in reasons[3]
    assert "tre lettere maiuscole" in reasons[4]
    assert "3 colonne" in reasons[5]


async def test_import_accounts_for_every_data_row() -> None:
    _, body = await post_import(MIXED_CSV)
    problems = body["problems"]
    assert isinstance(problems, list)
    data_rows = len([line for line in MIXED_CSV.splitlines()[1:] if line.strip()])
    assert body["imported_count"] == data_rows - len(problems)


async def test_header_only_file_imports_nothing_without_inventing_problems() -> None:
    status_code, body = await post_import("date,description,amount,currency\n")
    assert status_code == 200
    assert body == {"imported_count": 0, "problems": []}


async def test_blank_lines_do_not_shift_row_numbers() -> None:
    csv_with_gap = (
        "date,description,amount,currency\n"
        "2025-01-05,Bonifico Enel,85.50,EUR\n"
        "\n"
        "2025-01-09,Cipolla,0.00,EUR\n"
    )
    _, body = await post_import(csv_with_gap)
    problems = body["problems"]
    assert isinstance(problems, list)
    assert [problem["row"] for problem in problems] == [4]


async def test_empty_file_is_a_domain_error_in_the_standard_envelope() -> None:
    status_code, body = await post_import("   \n")
    assert status_code == 400
    assert body["error"] == "EMPTY_IMPORT_FILE"
    assert set(body) == ENVELOPE_KEYS
    assert body["status"] == 400


async def test_file_that_is_not_a_csv_is_a_domain_error() -> None:
    status_code, body = await post_import("Questo e' un memo, non un estratto conto.\n")
    assert status_code == 400
    assert body["error"] == "INVALID_CSV_HEADER"
    assert set(body) == ENVELOPE_KEYS


async def test_malformed_quoting_is_a_domain_error_not_a_crash() -> None:
    broken = 'date,description,amount,currency\n2025-01-05,"Bonifico Enel,EUR\n'
    status_code, body = await post_import(broken)
    assert status_code == 400
    assert body["error"] == "IMPORT_FILE_NOT_CSV"
    assert set(body) == ENVELOPE_KEYS


async def test_missing_file_part_is_422_naming_the_field() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/ai/movements/import")
    assert response.status_code == 422
    body: dict[str, object] = response.json()
    details = body["details"]
    assert isinstance(details, list)
    assert any("file" in str(detail) for detail in details)
    assert set(body) == ENVELOPE_KEYS | {"details"}


async def test_error_bodies_never_leak_traceback_or_local_paths() -> None:
    for content in ("", "memo\n", 'a,b,c,d\n"x,y\n'):
        _, body = await post_import(content)
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

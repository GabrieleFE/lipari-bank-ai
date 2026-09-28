"""L'estensione del pomeriggio: l'import che si puo' rilanciare.

La proprieta' dichiarata e' una sola, ed e' questa:

    Lo stesso file caricato due volte con la stessa `Idempotency-Key` non raddoppia
    niente: la seconda risposta porta `replayed: true` e lo stesso conto.

Il resto del file e' il contorno che rende la proprieta' verificabile per davvero
(conflitti, chiavi diverse, chiavi malformate, assenza di chiave).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app

GIORNO = (
    "date,description,amount,currency\n"
    "2025-01-05,Bonifico Enel Energia,85.50,EUR\n"
    "2025-01-07,Esselunga supermercato,45.20,EUR\n"
    "2025-01-09,Pasticceria Centrale,4.20,EUR\n"
)
GIORNO_DOPO = (
    "date,description,amount,currency\n"
    "2025-01-05,Bonifico Enel Energia,85.50,EUR\n"
    "2025-01-08,Bar Aurora,3.10,EUR\n"
)


def upload(content: str, filename: str = "estratto.csv") -> dict[str, tuple[str, bytes, str]]:
    return {"file": (filename, content.encode("utf-8"), "text/csv")}


async def post(content: str, key: str | None = None) -> tuple[int, dict[str, object]]:
    headers = {"Idempotency-Key": key} if key is not None else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/movements/import",
            files=upload(content),
            headers=headers,
        )
    body: dict[str, object] = response.json()
    return response.status_code, body


async def test_ricaricare_lo_stesso_file_con_la_stessa_chiave_non_raddoppia_il_conto() -> None:
    """La proprieta' dell'estensione, in un test.

    Non si conta 'quante volte e' successo qualcosa": si confronta il conto. Il primo
    caricamento dichiara 3 righe, il rilancio dichiara le stesse 3 righe e dice che
    e' un rilancio. Se un giorno il rilancio diventasse un secondo caricamento, qui
    fallirebbe, perche' `replayed` tornerebbe `false`.
    """
    primo_status, primo = await post(GIORNO, key="caricamento-2025-01-06")
    secondo_status, secondo = await post(GIORNO, key="caricamento-2025-01-06")

    assert (primo_status, secondo_status) == (200, 200)
    assert primo["replayed"] is False
    assert secondo["replayed"] is True

    # Stesso conto: righe dentro, righe fuori, totale. Campo per campo.
    for campo in ("imported_count", "total_rows", "problems"):
        assert secondo[campo] == primo[campo], f"il rilancio ha cambiato {campo}"


async def test_il_rilancio_restituisce_lo_stesso_problema_sulla_stessa_riga() -> None:
    """Il rilancio non e' una risposta generica: torna la stessa riga, con lo stesso numero."""
    _, primo = await post(
        "date,description,amount,currency\n2025-02-30,Cipolla,8.00,EUR\n", key="k"
    )
    _, secondo = await post(
        "date,description,amount,currency\n2025-02-30,Cipolla,8.00,EUR\n", key="k"
    )

    problems = secondo["problems"]
    assert isinstance(problems, list)
    assert problems == primo["problems"]
    assert problems[0]["row"] == 2


async def test_stessa_chiave_con_file_diverso_e_un_conflitto_non_un_riavvio() -> None:
    """Il caso che fa la differenza: non confondere 'rilancio' con 'file nuovo'.

    Se la stessa chiave potesse descrivere due contenuti diversi, l'ufficio
    riceverebbe il conto del file sbagliato senza nessun modo di accorgersene. Meglio
    un 409 che dice cosa e' successo.
    """
    status_code, first = await post(GIORNO, key="k-unica")
    assert status_code == 200
    assert first["imported_count"] == 3

    conflict_status, conflict = await post(GIORNO_DOPO, key="k-unica")
    assert conflict_status == 409
    assert conflict["error"] == "IDEMPOTENCY_KEY_CONFLICT"
    details = conflict["details"]
    assert isinstance(details, list)
    assert details[0]["field"] == "Idempotency-Key"


async def test_il_rilancio_solo_con_lo_stesso_contenuto_e_con_lo_stesso_nome_e_il_riavvio() -> None:
    """Lo stesso contenuto con un nome diverso e' comunque lo stesso file.

    Il nome non partecipa al giudizio: l'ufficio rinomina l'estratto ogni giorno e il
    giudizio deve stare sul contenuto, che e' l'unica cosa che non cambia.
    """
    _, primo = await post(GIORNO, key="k")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai/movements/import",
            files=upload(GIORNO, filename="estratto_conto_06_01_2025_rifatto.csv"),
            headers={"Idempotency-Key": "k"},
        )
    assert response.json()["replayed"] is True
    assert response.json()["imported_count"] == primo["imported_count"]


async def test_una_chiave_diversa_rende_il_caricamento_un_nuovo_e_non_un_riavvio() -> None:
    """Senza chiave, o con un'altra chiave, non si promette niente: e' un caricamento nuovo."""
    _, primo = await post(GIORNO, key="caricamento-a")
    _, secondo = await post(GIORNO, key="caricamento-b")
    assert primo["replayed"] is False
    assert secondo["replayed"] is False


async def test_senza_chiave_l_import_e_fatto_e_il_contratto_non_inventa_nessuna_garanzia() -> None:
    """Nessuna chiave dichiarata = nessuna promessa di idempotenza. Funziona lo stesso."""
    status_code, body = await post(GIORNO)
    assert status_code == 200
    assert body["replayed"] is False
    assert body["imported_count"] == 3


@pytest.mark.parametrize("chiave", ["", "   "])
async def test_una_chiave_vuota_e_un_errore_e_non_una_chiave_ignorata(chiave: str) -> None:
    """Una chiave vuota non viene trattata come 'nessuna chiave'.

    Ignorarla in silenzio vorrebbe dire: il chiamante crede di avere la garanzia del
    rilancio e in realta non ce l'ha. Torna indietro con il campo da correggere.
    """
    status_code, body = await post(GIORNO, key=chiave)
    assert status_code == 400
    assert body["error"] == "INVALID_IDEMPOTENCY_KEY"


async def test_una_chiave_troppo_lunga_e_un_errore() -> None:
    status_code, body = await post(GIORNO, key="k" * 201)
    assert status_code == 400
    assert body["error"] == "INVALID_IDEMPOTENCY_KEY"

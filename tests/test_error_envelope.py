"""L'estensione scelta: la validazione che dice la verita'.

La trappola dichiarata dalla consegna e' che il 422 che FastAPI genera da solo e
l'errore di dominio che solleva il servizio arrivano al chiamante in due forme
diverse, e per lui sono lo stesso problema. Questo file verifica che non lo siano.

La proprieta' dichiarata:

    Ogni errore dell'API, di qualunque origine, arriva nella stessa busta
    `ErrorResponse`, e ogni problema dentro `details` ha un `field` e un `code`
    machine-readable, perche' un programma possa contarlo senza leggere l'italiano.
"""

import csv
import io
from typing import Any

import pytest
from fastapi import UploadFile
from httpx import ASGITransport, AsyncClient

from src.api.movements import CHUNK_BYTES, read_capped
from src.exceptions import ImportFileError
from src.main import app

ENVELOPE = {"timestamp", "status", "error", "message", "path", "details"}
DETAIL = {"field", "code", "message", "expected", "received"}


Upload = dict[str, tuple[str, bytes, str]]


async def post_json(body: dict[str, object]) -> tuple[int, dict[str, Any]]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/ai/categorize", json=body)
    return response.status_code, response.json()


async def post_csv(upload: Upload | None) -> tuple[int, dict[str, Any]]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        if upload is None:
            response = await client.post("/api/ai/movements/import")
        else:
            response = await client.post("/api/ai/movements/import", files=upload)
    return response.status_code, response.json()


def csv_file(text: str, filename: str = "estratto.csv") -> Upload:
    return {"file": (filename, text.encode("utf-8"), "text/csv")}


async def test_il_422_di_fastapi_e_un_errore_di_domino_hanno_la_stessa_busta() -> None:
    """Il cuore della proprieta': due origini, un solo formato.

    A sinistra il 422 che FastAPI genera da solo, senza che nessuno lo chieda.
    A destra il 400 che il servizio solleva per un file che non e' un CSV, e che
    risale fino all'handler. Se i due corpi non avessero le stesse chiavi, un
    chiamante dovrebbe avere due modi di leggere lo stesso problema.
    """
    malformato_status, malformato = await post_json({"description": "Bolletta", "amount": -1})
    dominio_status, dominio = await post_csv(csv_file("Questo e' un memo, non un estratto.\n"))

    assert (malformato_status, dominio_status) == (422, 400)
    for corpo in (malformato, dominio):
        assert set(corpo) == ENVELOPE
        assert isinstance(corpo["details"], list)
        assert isinstance(corpo["error"], str) and corpo["error"]

    # Stessa busta, contenuto diverso: il 422 ha un campo da correggere, il 400 e'
    # sul file intero e non ha un campo singolo da indicare.
    assert [d["field"] for d in malformato["details"]] == ["amount"]
    assert dominio["details"] == []


async def test_ogni_problema_ha_campo_e_codice_nel_linguaggio_del_chiamante() -> None:
    """`field` e `code` sono la parte che un programma usa; `message` e' per chi legge.

    Se il codice non ci fosse, l'unico modo di distinguere un importo a zero da una
    valuta sbagliata sarebbe leggere la frase e riconoscerla a orecchio. E' il punto
    della consegna: gli errori del sistema dovranno essere contati da un programma.
    """
    _, corpo = await post_json({"description": "Bolletta Enel", "amount": 0, "currency": "euro-it"})
    assert corpo["status"] == 422
    details = corpo["details"]
    assert isinstance(details, list)

    per_campo = {d["field"]: d for d in details}
    assert per_campo["amount"]["code"] == "NOT_POSITIVE"
    assert per_campo["currency"]["code"] == "NOT_A_CURRENCY"
    for dettaglio in details:
        assert set(dettaglio) == DETAIL

    # Il codice non si ripete: ogni problema distinto ha un codice distinto.
    codici = [d["code"] for d in details]
    assert len(codici) == len(set(codici))


async def test_la_riga_scartata_usa_lo_stesso_vocabolario_del_422() -> None:
    """La stessa parola di codice compare in una riga di CSV e in un 422.

    Non e' una coerenza estetica: e' il motivo per cui l'ufficio e il programma di
    controllo parlano della stessa cosa. Un `NOT_A_DATE` e' un `NOT_A_DATE`, venga
    dalla richiesta o dal file.
    """
    testo = "date,description,amount,currency\n2025-02-30,Cipolla,8.00,EUR\n"
    _, corpo = await post_csv(csv_file(testo))

    assert corpo["imported_count"] == 0
    assert corpo["total_rows"] == 1
    problema = corpo["problems"][0]
    assert problema["row"] == 2
    assert problema["detail"] == {
        "field": "date",
        "code": "NOT_A_DATE",
        "message": "non è una data valida in formato AAAA-MM-GG",
        "expected": "AAAA-MM-GG",
        "received": "2025-02-30",
    }


async def test_la_riga_scartata_dice_campo_che_cosa_ce_era_e_che_cosa_ce_era_atteso() -> None:
    """Estensione 2 della consegna: quale campo, cosa c'era, cosa ci si aspettava."""
    testo = "date,description,amount,currency\n2025-01-05,Bonifico Enel,-60.00,eur\n"
    _, corpo = await post_csv(csv_file(testo))

    # Una voce per riga rifiutata, non una per errore: la riga e' una, e lo sportello
    # la sistema una volta. Il primo problema utile e' quello da cui si comincia.
    problemi = corpo["problems"]
    assert len(problemi) == 1
    problema = problemi[0]
    assert problema["row"] == 2
    dettaglio = problema["detail"]
    assert dettaglio["field"] == "amount"
    assert dettaglio["code"] == "NOT_POSITIVE"
    # Il valore ricevuto e' quello che stava nel file (`-60.00`), non quello che
    # Pydantic ne ha ricavato (`-60.0`): chi legge il conto vede l'estratto, non il
    # risultato dell'interpretazione.
    assert dettaglio["received"] == "-60.00"
    assert dettaglio["expected"] == "> 0"
    assert dettaglio["message"]
    # `reason` non e' una seconda fonte di verita': e' `detail` reso in italiano.
    assert "amount" in problema["reason"]
    assert "maggiore di zero" in problema["reason"]
    assert "-60.0" in problema["reason"]


async def test_ogni_riga_rifiutata_ha_una_voce_anche_se_ha_piu_problemi() -> None:
    """Il conto non si perde: sette righe sporche, sette voci, sette righe del file.

    Una riga con tre problemi resta una riga nel conto e una voce in `problems`, con il
    campo del primo da sistemare. Quando l'ufficio la ricarica, la stessa riga torna
    con il problema successivo: e' il modo in cui 'nessuna riga sparisce' resta vero
    anche su un file molto sporco.
    """
    testo = (
        "date,description,amount,currency\n"
        "2025-02-30,,-60.00,eur\n"
        "2025-01-05,Bonifico Enel,85.50,EUR\n"
    )
    _, corpo = await post_csv(csv_file(testo))
    assert corpo["total_rows"] == 2
    assert corpo["imported_count"] == 1
    assert len(corpo["problems"]) == 1
    assert corpo["problems"][0]["row"] == 2


async def test_una_riga_male_formata_il_campo_da_correggere_e_la_riga_anche() -> None:
    """Quando la riga non e' una riga, il campo da correggere non e' `date` o `amount`.

    Dichiarare `field: "amount"` su una riga con tre colonne insegnerebbe allo
    sportello a correggere un campo che non c'e'. Il campo e' `row` e il codice dice
    cosa non torna.
    """
    testo = "date,description,amount,currency\n2025-01-05,Bonifico Enel,85.50\n"
    _, corpo = await post_csv(csv_file(testo))

    dettaglio = corpo["problems"][0]["detail"]
    assert dettaglio["field"] == "row"
    assert dettaglio["code"] == "COLUMN_COUNT_MISMATCH"
    assert dettaglio["expected"] == "4 colonne"
    assert dettaglio["received"] == "3"


async def test_un_campo_che_il_contratto_non_prevede_e_un_problema_e_non_un_ignoto() -> None:
    """Un campo extra non viene ignorato in silenzio: e' un problema conteggibile."""
    _, corpo = await post_json(
        {"description": "Spesa", "amount": 10, "iban": "IT60X0542811101000000123456"}
    )
    assert corpo["status"] == 422
    assert {d["code"] for d in corpo["details"]} == {"UNEXPECTED"}
    assert corpo["details"][0]["field"] == "iban"


async def test_il_codice_e_il_messaggio_non_si_inventano_l_uno_il_contro_l_altro() -> None:
    """Lo stesso tipo di errore ha lo stesso codice in due punti diversi del sistema.

    Valuta a mano cosa produce Pydantic, poi passa dal modulo che traduce: se i due
    discordassero, il chiamante che conta gli errori per codice avrebbe due serie di
    numeri diversi per lo stesso problema.
    """
    from pydantic import ValidationError

    from src.types.categorize import CategorizeRequest
    from src.validation_bridge import details_from_validation_error

    with pytest.raises(ValidationError) as errore:
        CategorizeRequest.model_validate({"description": "Spesa", "amount": 10, "currency": "euro"})
    dal_contratto = details_from_validation_error(errore.value)
    assert [d.code for d in dal_contratto] == ["NOT_A_CURRENCY"]

    _, corpo = await post_json({"description": "Spesa", "amount": 10, "currency": "euro"})
    assert [d["code"] for d in corpo["details"]] == [d.code for d in dal_contratto]


async def test_la_risposta_d_errore_non_contiene_traceback_ne_percorsi_del_computer() -> None:
    """Nessuna delle vie d'errore lascia uscire dettagli del server."""
    casi: list[Upload | None] = [
        csv_file(""),
        csv_file("memo\n"),
        csv_file('a,b,c,d\n"x,y\n'),
        None,
    ]
    for upload in casi:
        _, corpo = await post_csv(upload)
        testo = str(corpo)
        assert corpo["status"] >= 400, f"atteso un errore, ricevuto {corpo}"
        for vietato in (
            "Traceback",
            "site-packages",
            "movements_import_service",
            "uvicorn",
            "C:\\",
        ):
            assert vietato not in testo, f"{vietato} comparso in {testo}"

    _, malformato = await post_json({"description": "", "amount": 0, "currency": 42})
    assert "Traceback" not in str(malformato)


async def test_un_file_di_dieci_volti_piu_grande_costa_un_chunk_e_non_la_memoria() -> None:
    """Il quarto rilievo della review: cosa accade con un file molto piu' grande.

    Non e' una prova di non-crash (quella la fa il `413` sull'endpoint): e' la prova
    che il limite viene applicato *durante* la lettura. Un `read()` senza argomento
    avrebbe gia' portato 10 MB in memoria prima di guardare la dimensione.
    """
    huge = b"x" * (10 * 1024 * 1024)
    upload = UploadFile(filename="enorme.csv", file=io.BytesIO(huge))
    with pytest.raises(ImportFileError) as errore:
        await read_capped(upload, limit=CHUNK_BYTES * 2)

    assert errore.value.status_code == 413
    assert errore.value.code == "IMPORT_FILE_TOO_LARGE"


async def test_il_csv_tornato_da_duecento_righe_ha_il_conto_esatto() -> None:
    """Duecento righe, sette sporche: il conto torna e nessuna riga sbagliata passa.

    Il file e' generato qui invece che tenuto in `data/` perche' la proprieta' da
    dimostrare e' aritmetica (`imported + scartate == righe`), non il contenuto di un
    file: se il file fosse fisso, un test che passa dimostrerebbe che quel file e'
    giusto, non che il conto torna.

    La richiesta passa dall'endpoint, non dal servizio: la promessa e' sul contratto
    HTTP, e un test che chiama il servizio non la verifica.
    """
    righe = 200
    # Sette problemi distinti, non sette copie: importi negativi e a zero, descrizione
    # vuota, data inesistente, valuta per esteso, importo non numerico, colonne in
    # piu'. Ogni riga sporca resta nel conto con il suo numero.
    # `N/D` e' il segno con cui un estratto conto indica un movimento non numerico.
    # `NaN` sembrerebbe piu' adatto, ma `float("NaN")` e' un NaN valido: la riga
    # verrebbe respinta come `NOT_POSITIVE` e due righe avrebbero lo stesso codice.
    sporche: dict[int, tuple[str, ...] | None] = {
        12: ("2025-01-06", "Bonifico", "-40.00", "EUR"),
        57: ("2025-01-06", "Bonifico", "0.00", "EUR"),
        99: ("2025-01-06", "", "10.00", "EUR"),
        141: ("2025-02-30", "Bonifico", "10.00", "EUR"),
        166: ("2025-01-06", "Bonifico", "10.00", "eur"),
        188: ("2025-01-06", "Bonifico", "N/D", "EUR"),
        199: ("2025-01-06", "Bonifico", "10.00", "EUR", "EUR"),
    }

    buffer = io.StringIO()
    scrittore = csv.writer(buffer, lineterminator="\n")
    scrittore.writerow(["date", "description", "amount", "currency"])
    for indice in range(1, righe + 1):
        if indice in sporche:
            riga = sporche[indice]
            if riga is None:
                continue
            scrittore.writerow(riga)
        else:
            scrittore.writerow(("2025-01-06", f"Bonifico {indice}", "10.50", "EUR"))

    testo = buffer.getvalue()
    righe_dati = len(testo.splitlines()) - 1
    assert righe_dati == righe

    status_code, body = await post_csv(csv_file(testo, filename="duecento-righe.csv"))
    assert status_code == 200
    assert body["total_rows"] == righe
    assert body["imported_count"] == righe - len(sporche) == 193
    # `row` e' il numero di riga del file: l'intestazione occupa la riga 1, quindi
    # la dodicesima riga di dati e' la tredicesima del file. E' il numero che lo
    # sportello cerca aprendo l'estratto, non un indice interno.
    assert [p["row"] for p in body["problems"]] == [n + 1 for n in sorted(sporche)]
    assert body["imported_count"] + len(body["problems"]) == body["total_rows"] == righe
    # Nessuna delle righe sporche e' passata: ogni problema punta a una riga che
    # esiste davvero nel file e a un codice, non a un testo libero.
    numeri = {n + 1 for n in sporche}
    for problema in body["problems"]:
        assert problema["detail"]["code"]
        assert problema["row"] in numeri

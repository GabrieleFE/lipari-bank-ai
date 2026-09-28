"""Un comando solo, nove verifiche, nessuna domanda da fare.

    uv run python scripts/day2_smoke.py

Lo script avvia l'app vera (uvicorn sulla porta indicata) e la interroga via HTTP,
non tramite oggetti Python in-process: quello che si vede qui e' cio' che vede
l'ufficio dal browser o da `curl`. Non serve alcuna chiave API per `/api/ai/categorize`,
perche' il provider di default e' la regola a parole chiave.

Le nove verifiche sono i criteri della consegna. Ognuna stampa cosa ha chiesto,
cosa ha ottenuto e cosa significa. Un esito non atteso ferma lo script con un
codice di uscita diverso da zero: chi lo lancia capisce subito dove guardare.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import os
import socket
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

RIGHE = 200
NUMERO_SPORCHE = 7
ATTESE_BUONE = RIGHE - NUMERO_SPORCHE

# Sette righe sporche, tutte con una causa diversa: e' la prova che la validazione
# distingue i problemi invece di dire sempre "riga non valida".
# Nota su `N/D`: e' il segno con cui un estratto conto segnala un movimento non
# numerico. `NaN` sembrerebbe piu' adatto ma non lo e': `float("NaN")` e' un NaN
# valido, quindi la riga verrebbe respinta come `NOT_POSITIVE` e due righe avrebbero
# lo stesso codice.
RIGHE_SPORCHE: dict[int, tuple[str, ...]] = {
    12: ("2025-01-06", "Bonifico", "-40.00", "EUR"),
    57: ("2025-01-06", "Bonifico", "0.00", "EUR"),
    99: ("2025-01-06", "", "10.00", "EUR"),
    141: ("2025-02-30", "Bonifico", "10.00", "EUR"),
    166: ("2025-01-06", "Bonifico", "10.00", "eur"),
    188: ("2025-01-06", "Bonifico", "N/D", "EUR"),
    199: ("2025-01-06", "Bonifico", "10.00", "EUR", "EUR"),
}

CODICI_ATTESI = {
    12: "NOT_POSITIVE",
    57: "NOT_POSITIVE",
    99: "EMPTY",
    141: "NOT_A_DATE",
    166: "NOT_A_CURRENCY",
    188: "NOT_A_NUMBER",
    199: "COLUMN_COUNT_MISMATCH",
}

BUSTA = {"timestamp", "status", "error", "message", "path", "details"}
DETTAGLIO = {"field", "code", "message", "expected", "received"}

_verifiche = 0
_fallite: list[str] = []


def esito(numero: int, titolo: str, ok: bool, cosa: str, atteso: str) -> None:
    global _verifiche
    _verifiche += 1
    segno = "OK  " if ok else "FAIL"
    print(f"[{segno}] {numero}. {titolo}")
    print(f"        chiesto: {cosa}")
    print(f"        atteso: {atteso}")
    if not ok:
        print("        RISULTATO NON CONFORME")
        _fallite.append(f"{numero}. {titolo}")
    print()


def porta_libera() -> int:
    """Trova una porta libera invece di sperare che la 8000 lo sia."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def app_avviata(porta: int) -> Iterator[None]:
    """Avvia uvicorn in un thread e aspetta che risponda davvero.

    `server.started` passa a True quando l'event loop e' pronto: senza questo
    controllo la prima richiesta arriva mentre il socket non e' ancora in ascolto e
    lo script fallirebbe per un motivo che non c'entra con la consegna.
    """
    import threading

    import uvicorn

    from src.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        limite = time.monotonic() + 30
        while time.monotonic() < limite:
            if server.started:
                break
            if not thread.is_alive():
                msg = "il processo del server e' morto durante l'avvio"
                raise RuntimeError(msg)
            time.sleep(0.05)
        else:
            msg = "il server non si e' avviato entro 30 secondi"
            raise RuntimeError(msg)
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def csv_duecento_righe() -> bytes:
    buffer = io.StringIO()
    scrittore = csv.writer(buffer, lineterminator="\n")
    scrittore.writerow(["date", "description", "amount", "currency"])
    for indice in range(1, RIGHE + 1):
        riga = RIGHE_SPORCHE.get(indice)
        scrittore.writerow(riga if riga else ("2025-01-06", f"Bonifico {indice}", "10.50", "EUR"))
    return buffer.getvalue().encode("utf-8")


def chiavi_ingestite(corpo: dict[str, object]) -> set[str]:
    details = corpo.get("details")
    if not isinstance(details, list):
        return set()
    return {str(d.get("code")) for d in details if isinstance(d, dict)}


async def verifica_uno_bolletta(base: str) -> None:
    """Una bolletta riconosciuta, con la confidenza dichiarata."""
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        risposta = await client.post(
            "/api/ai/categorize",
            json={"description": "Bonifico Enel Energia bolletta gennaio", "amount": 85.50},
        )
    corpo = risposta.json()
    categoria = corpo.get("category")
    confidenza = corpo.get("confidence")
    motivazione = str(corpo.get("reasoning", ""))
    esito(
        1,
        "Una bolletta viene classificata e la confidenza e' detta, non implicita",
        risposta.status_code == 200
        and categoria == "UTILITIES"
        and isinstance(confidenza, float)
        and confidenza > 0.5
        and "enel" in motivazione,
        'POST /api/ai/categorize {"description": "Bonifico Enel Energia", "amount": 85.50}',
        f"200 con category=UTILITIES, confidence > 0.5 e reasoning che cita la parola trovata "
        f"(ottenuto: {categoria}, {confidenza})",
    )


async def verifica_due_causale_sconosciuta(base: str) -> None:
    """Una causale senza regole torna OTHER con confidenza bassa, non silenzio."""
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        risposta = await client.post(
            "/api/ai/categorize",
            json={"description": "Bonifico prot. 2025/014 rimborso spese", "amount": 120},
        )
    corpo = risposta.json()
    categoria = corpo.get("category")
    confidenza = corpo.get("confidence")
    esito(
        2,
        "Una causale che nessuna regola conosce torna OTHER e lo dichiara",
        risposta.status_code == 200
        and categoria == "OTHER"
        and isinstance(confidenza, float)
        and confidenza < 0.5,
        'POST /api/ai/categorize {"description": "Bonifico prot. 2025/014", "amount": 120}',
        f"200 con category=OTHER e confidence < 0.5 (ottenuto: {categoria}, {confidenza})",
    )


async def verifica_tre_valuta_maiuscola(base: str) -> None:
    """ "usd" non e' EUR di default: e' una valuta scritta male, e va detto."""
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        risposta = await client.post(
            "/api/ai/categorize",
            json={"description": "Esselunga", "amount": 45.20, "currency": "usd"},
        )
    corpo = risposta.json()
    codici = chiavi_ingestite(corpo)
    esito(
        3,
        "Una valuta scritta per esteso torna 422 e nomina il campo",
        risposta.status_code == 422 and set(corpo) == BUSTA and codici == {"NOT_A_CURRENCY"},
        'POST /api/ai/categorize {"currency": "usd"}',
        f"422 con la busta unica e details[].code = NOT_A_CURRENCY (ottenuto: "
        f"{risposta.status_code}, {codici or corpo})",
    )


async def verifica_quattro_duecento_righe(base: str) -> None:
    """Il conto torna: 200 righe, 7 sporche, dentro + fuori = 200."""
    contenuto = csv_duecento_righe()
    async with httpx.AsyncClient(base_url=base, timeout=60) as client:
        risposta = await client.post(
            "/api/ai/movements/import",
            files={"file": ("duecento-righe.csv", contenuto, "text/csv")},
        )
    corpo = risposta.json()
    problemi = corpo.get("problems")
    problemi = problemi if isinstance(problemi, list) else []
    importate = corpo.get("imported_count")
    totali = corpo.get("total_rows")
    esito(
        4,
        "Un CSV da duecento righe torna con il conto esatto e sette scarti nominati",
        risposta.status_code == 200
        and totali == RIGHE
        and importate == ATTESE_BUONE
        and len(problemi) == NUMERO_SPORCHE
        and isinstance(importate, int)
        and importate + len(problemi) == totali,
        "POST /api/ai/movements/import con 200 righe di cui 7 non conformi",
        f"200 con total_rows=200, imported_count={ATTESE_BUONE} e 7 problemi "
        f"(ottenuto: {totali}, {importate}, {len(problemi)} problemi)",
    )

    numeri = [p.get("row") for p in problemi if isinstance(p, dict)]
    esito(
        5,
        "Ogni riga scartata torna con il suo numero di riga e un codice, non con un testo libero",
        numeri == sorted(n + 1 for n in RIGHE_SPORCHE),
        "i numeri di riga dei sette problemi della risposta precedente",
        f"{sorted(n + 1 for n in RIGHE_SPORCHE)} (la riga 1 e' l'intestazione, quindi la riga di "
        f"dati 12 e' la riga 13 del file) - ottenuto: {numeri}",
    )

    codici = {p["detail"]["code"] for p in problemi if isinstance(p, dict) and "detail" in p}
    attesi = set(CODICI_ATTESI.values())
    esito(
        6,
        "Le sette righe sporche hanno sette cause diverse: la validazione distingue, non ripete",
        codici == attesi,
        "i codici nei sette problemi",
        f"{sorted(attesi)} - ottenuto: {sorted(codici)}",
    )


async def verifica_sette_busta_unica(base: str) -> None:
    """422 di FastAPI, 400 di dominio e 400 di dominio: tre origini, un solo formato."""
    memo = b"Questo e' un memo, non un estratto conto.\n"
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        veloce = await client.post(
            "/api/ai/categorize", json={"description": "Bolletta", "amount": -1}
        )
        dominio = await client.post(
            "/api/ai/movements/import",
            files={"file": ("memo.txt", memo, "text/csv")},
        )
        vuoto = await client.post(
            "/api/ai/movements/import", files={"file": ("vuoto.csv", b"   \n", "text/csv")}
        )
    risposte = [veloce, dominio, vuoto]
    conformi = all(set(r.json()) == BUSTA and r.json()["status"] == r.status_code for r in risposte)
    dettagli = [d for r in risposte for d in r.json().get("details", []) if isinstance(d, dict)]
    esito(
        7,
        "422 di FastAPI, 400 di dominio e file vuoto: tre origini, una sola busta",
        conformi
        and {r.status_code for r in risposte} == {400, 422}
        and all(set(d) == DETTAGLIO for d in dettagli),
        "un 422 (importo negativo), un 400 (memo non CSV) e un 400 (file vuoto)",
        f"stesse chiavi {sorted(BUSTA)} in tutti e tre, `status` coerente col codice HTTP, e "
        f"ogni details[] con le cinque chiavi {sorted(DETTAGLIO)}",
    )


async def verifica_otto_niente_tracce(base: str) -> None:
    """Nessuna risposta d'errore lascia uscire un dettaglio del server."""
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        risposte = [
            await client.post(
                "/api/ai/movements/import",
                files={"file": ("memo.txt", b"memo\n", "text/csv")},
            ),
            await client.post(
                "/api/ai/movements/import",
                files={"file": ("rotto.csv", b'a,b,c,d\n"x,y\n', "text/csv")},
            ),
            await client.post("/api/ai/movements/import"),
            await client.post(
                "/api/ai/categorize",
                json={"description": "", "amount": 0, "currency": 42},
            ),
        ]
    vietati = ("Traceback", "site-packages", "uvicorn", "movements_import_service", "C:\\")
    testi = [r.text for r in risposte]
    colpiti = [v for v in vietati for t in testi if v in t]
    esito(
        8,
        "Nessuna risposta d'errore contiene un traceback o un percorso del computer",
        not colpiti and all(r.status_code >= 400 for r in risposte),
        "quattro richieste che falliscono per quattro motivi diversi",
        f"tutte con status >= 400 e nessuna di queste stringhe: {list(vietati)}"
        + (f" - trovate: {colpiti}" if colpiti else ""),
    )


async def verifica_nove_rilancio(base: str) -> None:
    """L'estensione: ricaricare non raddoppia, cambiare il file con la stessa chiave e' 409."""
    contenuto = csv_duecento_righe()
    altro = b"date,description,amount,currency\n2025-01-05,Bonifico Enel,85.50,EUR\n"
    chiave = "smoke-riavvio-1"
    async with httpx.AsyncClient(base_url=base, timeout=60) as client:
        primo = await client.post(
            "/api/ai/movements/import",
            files={"file": ("duecento-righe.csv", contenuto, "text/csv")},
            headers={"Idempotency-Key": chiave},
        )
        secondo = await client.post(
            "/api/ai/movements/import",
            files={"file": ("duecento-righe.csv", contenuto, "text/csv")},
            headers={"Idempotency-Key": chiave},
        )
        diverso = await client.post(
            "/api/ai/movements/import",
            files={"file": ("altro.csv", altro, "text/csv")},
            headers={"Idempotency-Key": chiave},
        )
    primo_c, secondo_c, diverso_c = primo.json(), secondo.json(), diverso.json()
    esito(
        9,
        "Ricaricare lo stesso file con la stessa chiave non raddoppia; cambiarlo e' un 409",
        primo.status_code == 200
        and primo_c.get("replayed") is False
        and secondo.status_code == 200
        and secondo_c.get("replayed") is True
        and secondo_c.get("imported_count") == primo_c.get("imported_count")
        and diverso.status_code == 409
        and diverso_c.get("error") == "IDEMPOTENCY_KEY_CONFLICT",
        f"lo stesso CSV due volte con Idempotency-Key={chiave}, "
        "poi un file diverso con la stessa chiave",
        f"prima 200 replayed=false, seconda 200 replayed=true con lo stesso "
        f"imported_count ({primo_c.get('imported_count')}), terza 409 "
        f"IDEMPOTENCY_KEY_CONFLICT (ottenuto: {primo.status_code}/{primo_c.get('replayed')}, "
        f"{secondo.status_code}/{secondo_c.get('replayed')}, "
        f"{diverso.status_code}/{diverso_c.get('error')})",
    )


async def verifica_browser(base: str) -> None:
    """La console si apre e contiene i due moduli della giornata."""
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        risposta = await client.get("/")
    testo = risposta.text
    esito(
        10,
        "La console web si apre e contiene i due moduli della giornata",
        risposta.status_code == 200
        and "/api/ai/categorize" in testo
        and "/api/ai/movements/import" in testo
        and "Idempotency-Key" in testo,
        f"GET {base}/",
        "200 con le card di categorizzazione e import, e il campo Idempotency-Key",
    )


def riepilogo() -> int:
    print("=" * 72)
    if _fallite:
        print(f"{len(_fallite)} verifiche su {_verifiche} non conformi:")
        for voce in _fallite:
            print(f"  - {voce}")
        return 1
    print(f"Tutte le {_verifiche} verifiche sono conformi.")
    return 0


async def esegui(base: str) -> None:
    print(f"API su {base}\n")
    print("=" * 72)
    print("LE NOVE VERIFICHE DELLA CONSEGNA, PIU' LA CONSOLE")
    print("=" * 72)
    print()
    await verifica_uno_bolletta(base)
    await verifica_due_causale_sconosciuta(base)
    await verifica_tre_valuta_maiuscola(base)
    await verifica_quattro_duecento_righe(base)
    await verifica_sette_busta_unica(base)
    await verifica_otto_niente_tracce(base)
    await verifica_nove_rilancio(base)
    await verifica_browser(base)


def main() -> int:
    # Il default gia' e' `rules`, ma lo si dichiara qui: lo script deve dire cosa
    # sta provando, anche se qualcuno cambia il default in futuro.
    os.environ.setdefault("CATEGORIZE_PROVIDER", "rules")
    porta = porta_libera()
    base = f"http://127.0.0.1:{porta}"
    with app_avviata(porta):
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(esegui(base))
    codice = riepilogo()
    if codice == 0:
        print()
        print(f"Per provarlo a mano: apri {base}/ in un browser.")
    return codice


if __name__ == "__main__":
    sys.exit(main())

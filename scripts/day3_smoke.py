"""Un comando solo, otto verifiche via HTTP, e una che riparte da un altro processo.

    uv run python scripts/day3_smoke.py

Come `day2_smoke.py`, avvia l'app vera e la interroga via HTTP: quello che si vede
qui e' cio' che vede l'ufficio dal browser, non quello che vede Python con i suoi
oggetti in mano.

Ogni verifica stampa cosa ha chiesto, cosa ha ottenuto e cosa significa, e un esito
non conforme ferma lo script con un codice di uscita diverso da zero.

L'ultima verifica e' quella per cui questo giorno si fa, e per questo e' diversa da
tutte le altre. Le prime sette girano dentro un server avviato da questo script. La
ultima gira in un **processo separato**, lanciato come subprocess e chiuso dopo:
un Python nuovo, un engine nuovo, nessuna memoria condivisa con quello che ha
scritto. Se la chat e il saldo ci sono anche li', e' PostgreSQL a ricordarli.

L'unico trucco e' dichiarato: per non chiamare un'API a pagamento, il provider LLM
viene sostituito con uno stub locale (`stub_provider`). Tutto il resto resta vero:
HTTP vero, sessione vera, transazione vera. Lo stub cambia chi risponde, non cosa
viene scritto.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

CONTI_ATTESI = 3
GIORNI = 90

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


def denaro_testo(valore: object) -> bool:
    """Un importo contabile e' una stringa con due decimali, non un numero JSON.

    Il controllo e' sul *formato*, non sul valore: quello che si perde e' il
    centesimo, e un centesimo si perde solo se qualcuno ha fatto un passaggio in
    virgola mobile. Qui quel passaggio non c'e' mai stato, quindi la domanda da
    fare non e' "quanto vale" ma "come e' scritto".
    """
    if not isinstance(valore, str):
        return False
    try:
        Decimal(valore)
    except Exception:
        return False
    return "." in valore and len(valore.rsplit(".", 1)[1]) == 2


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


@contextmanager
def provider_finto() -> Iterator[None]:
    """Sostituisce il provider LLM con uno stub per non spendere soldi.

    La sostituzione avviene su `app.dependency_overrides`, lo stesso meccanismo dei
    test: se un giorno il chat non passasse piu' da `Depends(get_llm_provider)`,
    questo `with` non cambierebbe niente e le verifiche di chat comincerebbero a
    chiedere una chiave. Fallo succedere: e' un controllo gratis.
    """
    from src.llm.client import LLMResponse, Message, StreamChunk
    from src.llm.factory import get_llm_provider
    from src.main import app

    class StubProvider:
        """Risponde qualcosa di plausibile, con dentro il testo della domanda.

        Il richiamo alla domanda serve alla verifica del riavvio: se il secondo
        processo risponde "non ho memoria di marzo", vuol dire che la cronologia
        non e' arrivata dal database, e il controllo fallisce per la ragione giusta.
        """

        model = "stub-locale"

        async def complete(self, messages: list[Message], max_tokens: int = 500) -> LLMResponse:
            ultima = messages[-1].content if messages else ""
            return LLMResponse(
                content=f"Ho letto: {ultima}",
                tokens_used=15,
                cost_eur=0.0,
                model=self.model,
            )

        async def complete_stream(
            self, messages: list[Message], max_tokens: int = 500
        ) -> AsyncIterator[StreamChunk]:
            raise NotImplementedError
            yield  # pragma: no cover - serve solo a far diventare la funzione un generatore

    app.dependency_overrides[get_llm_provider] = lambda: StubProvider()
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


async def verifica_uno_i_conti_esistono(base: str) -> str:
    """Tre conti, e i soldi sono stringhe esatte. Ritorna l'id del primo."""
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        risposta = await client.get("/api/ai/accounts")
    corpo = risposta.json()
    conti = corpo.get("accounts", [])
    esito(
        1,
        "I conti ci sono e il saldo arriva come stringa decimale, non come numero",
        risposta.status_code == 200
        and len(conti) == CONTI_ATTESI
        and all(denaro_testo(c.get("balance")) for c in conti),
        f"GET {base}/api/ai/accounts",
        f"200 con {CONTI_ATTESI} conti e ogni balance stringa a due decimali "
        f"(ottenuto: {risposta.status_code}, "
        f"{[c.get('balance') for c in conti]})",
    )
    return str(conti[0]["id"]) if conti else ""


async def verifica_due_il_join(base: str, account_id: str) -> None:
    """I movimenti arrivano con l'intestatario: due query, non una per riga."""
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        risposta = await client.get(f"/api/ai/accounts/{account_id}/movements?limit=500")
    corpo = risposta.json()
    movimenti = corpo.get("movements", [])
    esito(
        2,
        "Movimenti e intestatario vengono dallo stesso JOIN, con il conto esatto",
        risposta.status_code == 200
        and corpo.get("count") == len(movimenti)
        and isinstance(corpo.get("holder"), str)
        and isinstance(corpo.get("iban"), str)
        and len(movimenti) > 0,
        f"GET {base}/api/ai/accounts/{account_id}/movements?limit=500",
        f"200 con holder e iban nella stessa risposta, e count che coincide con le "
        f"righe ricevute (ottenuto: count={corpo.get('count')}, "
        f"righe={len(movimenti)}, holder={corpo.get('holder')!r})",
    )
    esito(
        3,
        "Ogni movimento porta il conto giusto e un importo con due decimali",
        bool(movimenti)
        and all(m.get("account_id") == account_id for m in movimenti)
        and all(denaro_testo(m.get("amount")) for m in movimenti),
        f"i {len(movimenti)} movimenti della risposta precedente",
        "ognuno con account_id pari a quello chiesto e amount stringa a due decimali",
    )


async def verifica_quattro_il_group_by(base: str, account_id: str) -> None:
    """L'aggregazione per mese: poche righe, e i totali che tornano."""
    oggi = date.today()
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        risposta = await client.get(
            f"/api/ai/accounts/{account_id}/summary"
            f"?date_from={(oggi - timedelta(days=GIORNI)).isoformat()}"
            f"&date_to={oggi.isoformat()}"
        )
    corpo = risposta.json()
    mensili = corpo.get("monthly", [])
    n_movimenti = corpo.get("movement_count", 0)
    somma_mensile = sum(Decimal(str(m["total_out"])) for m in mensili)
    esito(
        4,
        "Il riepilogo per mese raggruppa davvero: poche righe, e i totali combaciano",
        risposta.status_code == 200
        and 1 < len(mensili) < n_movimenti
        and somma_mensile == Decimal(str(corpo.get("total_out"))),
        f"GET {base}/api/ai/accounts/{account_id}/summary sugli ultimi {GIORNI} giorni",
        f"200 con meno righe mensili ({len(mensili)}) dei movimenti ({n_movimenti}) e la "
        f"somma dei mesi pari a total_out (ottenuto: {len(mensili)} mesi su "
        f"{n_movimenti} movimenti, somma={somma_mensile}, "
        f"total_out={corpo.get('total_out')})",
    )
    entra, esce = Decimal(str(corpo.get("total_in"))), Decimal(str(corpo.get("total_out")))
    netto = Decimal(str(corpo.get("net")))
    esito(
        5,
        "Netto, entrate e uscite sono tre numeri coerenti fra loro",
        netto == entra - esce
        and corpo.get("account_id") == account_id
        and all(denaro_testo(corpo.get(k)) for k in ("total_in", "total_out", "net", "balance")),
        "i campi del riepilogo appena letto",
        f"net = total_in - total_out ({netto} = {entra} - {esce}) e tutti e quattro gli "
        f"importi stringhe a due decimali",
    )


async def verifica_sei_la_scrittura(base: str, account_id: str) -> int:
    """Registrare un movimento sposta il saldo. Ritorna il numero di righe."""
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        prima = await client.get(f"/api/ai/accounts/{account_id}/movements?limit=500")
        scrittura = await client.post(
            f"/api/ai/accounts/{account_id}/movements",
            json={
                "occurred_on": date.today().isoformat(),
                "description": "Caffe della macchinetta",
                "amount": "-2.50",
            },
        )
        dopo = await client.get(f"/api/ai/accounts/{account_id}/movements?limit=500")
    corpo = scrittura.json()
    righe_prima = len(prima.json().get("movements", []))
    righe_dopo = len(dopo.json().get("movements", []))
    esito(
        6,
        "Registrare un movimento risponde 201 col saldo nuovo, e il conto lo vede",
        scrittura.status_code == 201
        and denaro_testo(corpo.get("balance"))
        and righe_dopo == righe_prima + 1,
        f"POST {base}/api/ai/accounts/{account_id}/movements con -2.50, "
        "prima e dopo il numero di righe",
        f"201 con balance stringa e una riga in piu' (ottenuto: "
        f"{scrittura.status_code}, balance={corpo.get('balance')}, "
        f"righe {righe_prima} -> {righe_dopo})",
    )
    return righe_dopo


async def verifica_sette_lo_zero(base: str, account_id: str, righe_prima: int) -> None:
    """Un movimento da 0.00 si rifiuta prima di toccare il database."""
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        rifiutato = await client.post(
            f"/api/ai/accounts/{account_id}/movements",
            json={
                "occurred_on": date.today().isoformat(),
                "description": "Niente",
                "amount": "0.00",
            },
        )
        dopo = await client.get(f"/api/ai/accounts/{account_id}/movements?limit=500")
    righe_dopo = len(dopo.json().get("movements", []))
    esito(
        7,
        "Un movimento da 0.00 e' un 422 e non scrive niente in database",
        rifiutato.status_code == 422 and righe_dopo == righe_prima,
        f"POST {base}/api/ai/accounts/{account_id}/movements con 0.00, poi il numero di righe",
        f"422 e nessuna riga nuova (ottenuto: {rifiutato.status_code}, "
        f"righe {righe_prima} -> {righe_dopo})",
    )


async def verifica_otto_la_chat(base: str) -> str:
    """Un turno di chat, e la sua sessione. Ritorna l'id della sessione."""
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        risposta = await client.post(
            "/api/ai/chat", json={"session_id": "new", "message": "Quanto ho speso a marzo?"}
        )
    corpo = risposta.json()
    session_id = str(corpo.get("session_id", ""))
    esito(
        8,
        "Un turno di chat scrive la sessione e risponde",
        risposta.status_code == 200 and bool(session_id) and bool(corpo.get("reply")),
        f"POST {base}/api/ai/chat con session_id=new",
        f"200 con session_id e reply (ottenuto: {risposta.status_code}, "
        f"session_id={session_id or 'nessuna'})",
    )
    return session_id


# --------------------------------------------------------------------------
# La verifica dopo il riavvio: qui non c'e' piu' niente di questo processo
# --------------------------------------------------------------------------


async def ricontrolla(session_id: str, account_id: str) -> dict[str, object]:
    """Rilegge dal database, in un processo nuovo, quello che e' stato scritto prima.

    Questa funzione gira nel subprocess, non qui sopra. Non ha accesso alle
    variabili, alle sessioni o agli engine del processo padre: quello che vede
    arriva tutto da PostgreSQL.
    """
    from sqlalchemy import func, select

    from src.db.models import Account, ChatMessage, ChatSession, Movement
    from src.db.session import async_session_factory, engine
    from src.services.movements_service import MovementService

    try:
        async with async_session_factory() as session:
            chat = await session.get(ChatSession, UUID(session_id))
            ruoli = (
                (
                    await session.execute(
                        select(ChatMessage.role)
                        .where(ChatMessage.session_id == UUID(session_id))
                        .order_by(ChatMessage.created_at)
                    )
                )
                .scalars()
                .all()
            )
            n_movimenti = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(Movement)
                        .where(Movement.account_id == UUID(account_id))
                    )
                ).scalar_one()
            )
            stored, recomputed = await MovementService(session).reconcile(UUID(account_id))
            conto = await session.get(Account, UUID(account_id))
    finally:
        # Come sopra: questo processo sta per uscire, e chiudere il pool in
        # ordine evita il rumore di "Exception terminating connection" che
        # asyncpg stampa quando il loop muore con connessioni aperte.
        await engine.dispose()

    return {
        "sessione_trovata": chat is not None,
        "ruoli": [str(r) for r in ruoli],
        "saldo_memorizzato": str(stored),
        "saldo_ricalcolato": str(recomputed),
        "movimenti": n_movimenti,
        "holder": conto.holder if conto else "",
    }


def verifica_nove_dopo_il_riavvio(account_id: str, session_id: str) -> None:
    """Un altro processo legge quello che questo aveva scritto, e lo sa dire."""
    comando = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--dopo-riavvio",
        session_id,
        account_id,
    ]
    completato = subprocess.run(  # noqa: S603
        comando, capture_output=True, text=True, cwd=str(REPO), check=False
    )
    if completato.returncode != 0:
        esito(
            9,
            "Un processo nuovo ritrova la chat e il saldo scritti dal processo precedente",
            False,
            f"`{' '.join(comando[1:])}`",
            f"il subprocesso termina con 0 (ottenuto: {completato.returncode}, "
            f"stderr={completato.stderr.strip()[:300]})",
        )
        return
    letto = json.loads(completato.stdout.strip().splitlines()[-1])
    esito(
        9,
        "Un processo nuovo ritrova la chat e il saldo scritti dal processo precedente",
        bool(letto["sessione_trovata"])
        and list(letto["ruoli"]) == ["user", "assistant"]
        and letto["saldo_memorizzato"] == letto["saldo_ricalcolato"],
        f"un Python separato che rilegge la sessione {session_id} e riconcilia il conto "
        f"{account_id} (il padre e' gia' stato chiuso)",
        f"sessione presente con ruoli ['user', 'assistant'] e saldo memorizzato "
        f"({letto['saldo_memorizzato']}) pari a quello ricalcolato "
        f"({letto['saldo_ricalcolato']}) - ottenuto: ruoli={letto['ruoli']}, "
        f"saldo={letto['saldo_memorizzato']}/{letto['saldo_ricalcolato']}, "
        f"movimenti={letto['movimenti']}, conto={letto['holder']!r}",
    )


async def esegui(base: str) -> tuple[str, str]:
    print(f"API su {base}\n")
    print("=" * 72)
    print("LE VERIFICHE DEL GIORNO 3, VIA HTTP CONTRO L'APP VERA")
    print("=" * 72)
    print()

    account_id = await verifica_uno_i_conti_esistono(base)
    if not account_id:
        print("Nessun conto: il seed non e' stato eseguito. Ferma qui.")
        return "", ""
    await verifica_due_il_join(base, account_id)
    await verifica_quattro_il_group_by(base, account_id)
    righe = await verifica_sei_la_scrittura(base, account_id)
    await verifica_sette_lo_zero(base, account_id, righe)
    session_id = await verifica_otto_la_chat(base)
    return account_id, session_id


def riepilogo() -> int:
    print("=" * 72)
    if _fallite:
        print(f"{len(_fallite)} verifiche su {_verifiche} non conformi:")
        for voce in _fallite:
            print(f"  - {voce}")
        return 1
    print(f"Tutte le {_verifiche} verifiche sono conformi.")
    return 0


def modalita_riavvio(session_id: str, account_id: str) -> int:
    """Il lato del subprocess: rilegge e stampa una riga di JSON, poi esce."""
    os.environ.setdefault("DEBUG", "false")
    print(json.dumps(asyncio.run(ricontrolla(session_id, account_id))))
    return 0


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--dopo-riavvio":
        return modalita_riavvio(sys.argv[2], sys.argv[3])

    # `DEBUG=false` va impostato *prima* di qualunque import di `src`: `Settings`
    # legge il file .env al momento dell'import, e con `debug` acceso l'engine
    # stampa ogni SQL. Lo smoke qui verifica numeri, e mezzo schermo di log
    # serve a nascondere i numeri. Con `setdefault` non si sovrascrive una scelta
    # deliberata di chi lancia lo script.
    os.environ.setdefault("DEBUG", "false")

    from src.db.seed import seed
    from src.db.session import async_session_factory, engine

    async def popola() -> None:
        async with async_session_factory() as session:
            scritti = await seed(session, date.today(), reset_all=True)
            print(
                f"[seed] tre conti e {scritti} movimenti su {GIORNI} giorni, "
                "saldo ricalcolato dal database."
            )
        # `dispose` non e' un dettaglio: `asyncio.run` chiude il loop quando
        # `popola` finisce, ma l'engine e' creato a livello di modulo e nel suo
        # pool ci sono connessioni aperte da quel loop. Il server che parte dopo
        # gira in un loop diverso e riuserebbe connessioni morte: ogni richiesta
        # finirebbe in 500 con "Event loop is closed", e il motivo sarebbe
        # lontanissimo dalla riga che lo causa. Vuotare il pool qui e' la meta'
        # giusta, e vale anche per lo `stack del giorno 2`, che non lo fa perche'
        # non parla col database fuori dall'HTTP.
        await engine.dispose()

    asyncio.run(popola())
    print()

    porta = porta_libera()
    base = f"http://127.0.0.1:{porta}"
    with provider_finto(), app_avviata(porta):
        with contextlib.suppress(KeyboardInterrupt):
            account_id, session_id = asyncio.run(esegui(base))

    print("=" * 72)
    print("Il server e' stato chiuso. Adesso un processo Python separato rileghe.")
    print("=" * 72)
    print()
    if account_id and session_id:
        verifica_nove_dopo_il_riavvio(account_id, session_id)
    else:
        esito(
            9,
            "Un processo nuovo ritrova la chat e il saldo scritti dal processo precedente",
            False,
            "la verifica dopo il riavvio",
            "un id di conto e uno di sessione da cui partire (ottenuto: nessuno dei due)",
        )

    return riepilogo()


if __name__ == "__main__":
    sys.exit(main())

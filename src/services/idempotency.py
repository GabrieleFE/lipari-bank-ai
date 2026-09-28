"""Idempotenza dichiarata dal chiamante.

Lo sportello carica un file ogni mattina. Se la rete risponde male e la richiesta
viene rilanciata, senza questo il conto si riempie due volte. La domanda che la
consegna lascia aperta e' *cosa vuol dire 'lo stesso file'*: il nome no (lo stesso
estratto si chiama ogni giorno diversamente), il contenuto no (un file diverso con
lo stesso nome e' un file diverso), l'identificativo che dichiara il chiamante si.

E' la risposta che danno i sistemi di pagamento, perche' e' l'unica che non indovina:
chi rilancia sa di star rilanciando, e sa quale caricamento sta rilanciando.
"""

import hashlib
import threading
from dataclasses import dataclass
from typing import Final, Protocol

from src.exceptions import IdempotencyKeyError

MAX_KEY_LENGTH: Final = 200


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """Che cosa e' successo la prima volta che questa chiave e' arrivata."""

    content_hash: str


class IdempotencyStore(Protocol):
    def claim(self, key: str, content_hash: str) -> IdempotencyRecord | None: ...


class InMemoryIdempotencyStore:
    """Registro in memoria, con capienza.

    Un solo processo, una sola vita: se il processo riparte le chiavi spariscono.
    E' il punto debole dichiarato dell'estensione, non un dettaglio nascosto
    (`docs/ai-review/G2.md`, rilievo 12). La `Protocol` esiste perche' il prossimo
    passo e' un `INSERT ... ON CONFLICT` su PostgreSQL, che si sostituisce qui senza
    toccare l'endpoint.
    """

    def __init__(self, *, max_keys: int = 10_000) -> None:
        self._max_keys = max_keys
        self._records: dict[str, IdempotencyRecord] = {}
        self._lock = threading.Lock()

    def claim(self, key: str, content_hash: str) -> IdempotencyRecord | None:
        """Riserva la chiave in modo atomico. Restituisce il record se esiste gia'.

        Il `claim` e' atomico perche' due richieste con la stessa chiave che arrivano
        insieme non possano entrambe entrare: la seconda trova la chiave gia' presa e
        si comporta da rilancio. Senza il lock, l'ufficio che carica due volte lo
        stesso file da due schede diverse raddoppia lo stesso conto.

        L'eviction e' per inserimento e non per tempo: l'ufficio carica un file al
        giorno, quindi la chiave piu' vecchia e' la prima, ed e' quella che nessuno
        rilancia piu'.
        """
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                return existing
            if len(self._records) >= self._max_keys:
                self._records.pop(next(iter(self._records)))
            self._records[key] = IdempotencyRecord(content_hash=content_hash)
            return None

    def clear(self) -> None:
        """Svuota il registro. Serve ai test, che altrimenti si troverebbero la
        chiave di un test precedente gia' occupata."""
        with self._lock:
            self._records.clear()


def content_hash(raw: bytes) -> str:
    """L'impronta del contenuto, non del nome: due nomi diversi, stesso file = stesso hash."""
    return hashlib.sha256(raw).hexdigest()


def normalize_key(raw: str | None) -> str | None:
    """La chiave arriva come header: stringa libera, ma non arbitraria.

    Vuota o troppo lunga non e' una chiave, e' una chiave dimenticata o un file
    inviato al posto dell'header. Torna indietro come errore di dominio invece di
    venire silenziosamente ignorata e poi lamentarsi che l'import raddoppia.
    """
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        raise IdempotencyKeyError("arrivata vuota")
    if len(key) > MAX_KEY_LENGTH:
        raise IdempotencyKeyError(f"piu' lunga di {MAX_KEY_LENGTH} caratteri")
    return key

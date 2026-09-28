"""Orchestrazione dell'import: il file entra, il conto torna, e si puo' rilanciare.

Il punto in cui si decide se una richiesta e' un caricamento nuovo o un rilancio
e' qui, non nell'endpoint. L'endpoint non deve sapere cosa sia una chiave
d'idempotenza: deve sapere che gli si danno dei byte e che gli torna un conto. La
regola e' nel servizio perche' e' una regola di dominio, e un dominio si chiama da uno
script, da un job, da un test, non solo da una richiesta HTTP.
"""

from src.exceptions import IdempotencyConflictError
from src.services.idempotency import IdempotencyStore, content_hash, normalize_key
from src.services.movements_import_service import MovementsImportService
from src.types.movements import MovementImportResponse


class MovementImportRunner:
    """Il contratto dell'endpoint, con in piu' la risposta alla domanda sul rilancio.

    `replayed` sta dentro `MovementImportResponse` e non in un wrapper: se l'endpoint
    avesse un envelope esterno, il `response_model` taglierebbe il campo e il chiamante
    riceverebbe un conto senza sapere se e' un caricamento o un rilancio. Il
    `response_model` decide in silenzio quello che non dichiari: quindi si dichiara.
    """

    def __init__(self, importer: MovementsImportService, store: IdempotencyStore) -> None:
        self._importer = importer
        self._store = store

    def run(self, raw: bytes, idempotency_key: str | None) -> MovementImportResponse:
        key = normalize_key(idempotency_key)
        replayed = False
        if key is not None:
            digest = content_hash(raw)
            first = self._store.claim(key, digest)
            if first is not None and first.content_hash != digest:
                raise IdempotencyConflictError(key, first.content_hash, digest)
            replayed = first is not None
        # Rilancio o no, il conto lo ricalcola la validazione: la risposta non
        # dipende da quando e' stato scritto il registro, e un rilancio non puo'
        # restituire un risultato diverso da quello della prima volta.
        return self._importer.import_csv(raw).model_copy(update={"replayed": replayed})

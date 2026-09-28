"""Vocabolario unico degli errori di validazione.

Un solo posto dove si decide *come* si nomina un problema di validazione, in modo che
il 422 che FastAPI produce da solo e l'errore di dominio che solleva il servizio
diano la stessa risposta al chiamante: stessi campi, stessi codici, stessa frase.

Regola del giorno: la busta di errore e' un contratto, non un messaggio. Un programma
deve poter contare gli errori per codice e individuarli per campo, senza fare parsing
di italiano.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

MAX_ECHOED_VALUE: Final = 50
UNKNOWN_CODE: Final = "INVALID_VALUE"
UNKNOWN_FIELD: Final = "body"


@dataclass(frozen=True, slots=True)
class Hint:
    """Come tradurre un errore di Pydantic: codice, frase e contratto atteso."""

    code: str
    message: str
    expected: str


# Per tipo di errore Pydantic: il caso generale.
_BY_TYPE: Final[dict[str, Hint]] = {
    "greater_than": Hint("NOT_POSITIVE", "deve essere maggiore di zero", "> 0"),
    "less_than": Hint("OUT_OF_RANGE", "deve essere minore del limite indicato", "< limite"),
    "greater_than_equal": Hint(
        "OUT_OF_RANGE", "non può essere minore del limite indicato", ">= limite"
    ),
    "string_too_short": Hint("EMPTY", "non può essere vuota", "almeno 1 carattere"),
    "string_too_long": Hint("TOO_LONG", "è troppo lunga", "al massimo 200 caratteri"),
    "float_parsing": Hint("NOT_A_NUMBER", "deve essere un numero", "numero decimale"),
    "float_type": Hint("NOT_A_NUMBER", "deve essere un numero", "numero decimale"),
    "int_parsing": Hint("NOT_AN_INTEGER", "deve essere un numero intero", "intero"),
    "int_type": Hint("NOT_AN_INTEGER", "deve essere un numero intero", "intero"),
    "bool_parsing": Hint("NOT_A_BOOLEAN", "deve essere true o false", "true | false"),
    "bool_type": Hint("NOT_A_BOOLEAN", "deve essere true o false", "true | false"),
    "missing": Hint("MISSING", "manca", "campo obbligatorio"),
    "extra": Hint("UNEXPECTED", "non è previsto dal contratto", "nessun campo extra"),
    "extra_forbidden": Hint("UNEXPECTED", "non è previsto dal contratto", "nessun campo extra"),
    "literal_error": Hint("NOT_ALLOWED", "non è un valore ammesso", "uno dei valori ammessi"),
    "date_type": Hint("NOT_A_DATE", "non è una data valida", "AAAA-MM-GG"),
}

# Per (tipo, campo): i due casi in cui la stessa forma ha un contratto diverso.
# La data deve *esistere* nel calendario (2025-02-30 non e' una data) e la valuta ha
# un formato proprio: senza questi override la stessa eccezione di Pydantic direbbe
# "formato non valido" a un chiamante che invece ha mandate una data inesistente.
_BY_FIELD_TYPE: Final[dict[tuple[str, str], Hint]] = {
    ("date_parsing", "date"): Hint(
        "NOT_A_DATE", "non è una data valida in formato AAAA-MM-GG", "AAAA-MM-GG"
    ),
    ("date_from_datetime_parsing", "date"): Hint(
        "NOT_A_DATE", "non è una data valida in formato AAAA-MM-GG", "AAAA-MM-GG"
    ),
    ("string_pattern_mismatch", "currency"): Hint(
        "NOT_A_CURRENCY", "deve essere un codice valuta di tre lettere maiuscole", "[A-Z]{3}"
    ),
}

_FALLBACK: Final = Hint(UNKNOWN_CODE, "non rispetta il formato atteso", "formato atteso")


def hint_for(error_type: str, field: str) -> Hint:
    """Prima il caso specifico (tipo, campo), poi il tipo generico, poi il fallback."""
    specific = _BY_FIELD_TYPE.get((error_type, field))
    if specific is not None:
        return specific
    return _BY_TYPE.get(error_type, _FALLBACK)


def field_path(loc: Sequence[object]) -> str:
    """`("body", "file")` -> `"file"`, `("body", "row", "amount")` -> `"row.amount"`.

    Si tiene l'ultimo segmento non strutturale perche' per il chiamante il campo
    da correggere e' quello, non il percorso interno del validatore.
    """
    parts = [str(part) for part in loc if str(part) not in {"body", "query", "path"}]
    return ".".join(parts) if parts else UNKNOWN_FIELD


def terminal_field(loc: Sequence[object]) -> str:
    """Solo l'ultimo segmento: serve a scegliere l'hint (`currency`, `date`, ...)."""
    parts = [str(part) for part in loc if str(part) not in {"body", "query", "path"}]
    return parts[-1] if parts else UNKNOWN_FIELD


def echo_value(value: object) -> str:
    """Il valore ricevuto, in forma breve e leggibile.

    Serve a chi legge il conto ('cosa c'era'), non a un parser: il valore strutturato
    del chiamante e' gia' nel suo body, qui ne serve solo una traccia. Mai il traceback,
    mai il percorso di un file del server: solo il dato di input, troncato.
    """
    if value is None:
        return "assente"
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} byte non leggibili>"
    text = " ".join(str(value).split())
    if not text:
        return "vuoto"
    if len(text) > MAX_ECHOED_VALUE:
        return f"{text[:MAX_ECHOED_VALUE]}..."
    return text


def describe_detail(detail: Mapping[str, object]) -> str:
    """Proiezione umana di un dettaglio: la frase che legge l'operatore dello sportello.

    Derivata, non scritta a mano: `detail` resta l'unica fonte di verita e questa e'
    la sua resa in italiano. Cambiare il formato del dettaglio non richiede di venire
    qui a sistemare le stringhe.
    """
    parts = [f"{detail['field']}: {detail['message']}"]
    expected = detail.get("expected")
    if isinstance(expected, str) and expected:
        parts.append(f"atteso {expected}")
    received = detail.get("received")
    if isinstance(received, str) and received:
        parts.append(f"valore ricevuto: {received}")
    return f"{' '.join(parts)}"

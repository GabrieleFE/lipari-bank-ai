from collections.abc import Sequence

from pydantic import ValidationError

from src.types.error import ErrorDetail
from src.validation import describe_detail, echo_value, field_path, hint_for


def _loc_to_str(loc: object) -> str:
    if isinstance(loc, (str, int)):
        return str(loc)
    if isinstance(loc, tuple):
        return ".".join(str(part) for part in loc)
    return str(loc)


def details_from_validation_error(exc: ValidationError) -> list[ErrorDetail]:
    """`ValidationError` di Pydantic -> dettagli con lo stesso formato della busta.

    Unico ponte fra il dizionario degli errori di Pydantic e il nostro contratto: e'
    l'unico punto in cui si legge `type`, `loc` e `input` grezzi.
    """
    details: list[ErrorDetail] = []
    for error in exc.errors(include_url=False):
        hint = hint_for(str(error["type"]), _loc_to_str(error["loc"]))
        details.append(
            ErrorDetail(
                field=field_path(_as_sequence(error["loc"])),
                code=hint.code,
                message=hint.message,
                expected=hint.expected,
                received=echo_value(error.get("input")),
            )
        )
    return details


def _as_sequence(loc: object) -> Sequence[object]:
    if isinstance(loc, (str, int)):
        return (loc,)
    if isinstance(loc, Sequence):
        return loc
    return ()


def describe(error: ValidationError) -> str:
    """Frase leggibile per un `ValidationError` (log, console, riga di un CSV)."""
    rendered = [
        describe_detail(detail.model_dump()) for detail in details_from_validation_error(error)
    ]
    return "; ".join(rendered)

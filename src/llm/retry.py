"""Retry con backoff esponenziale centralizzato (tenacity).

Gli SDK (openai/anthropic) ritentano gia' al loro interno: per evitare retry doppi
(SDK + wrapper) che moltiplicano latenza e costo, disattiviamo il retry interno con
`max_retries=0` sul client e lasciamo a tenacity il compito di ritentare sugli errori
transitori (rate limit, timeout, errori di connessione).
"""

from collections.abc import Callable
from typing import Any

from tenacity import retry as tenacity_retry
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

MAX_ATTEMPTS = 4


def llm_retry(
    *exception_types: type[BaseException],
    max_attempts: int = MAX_ATTEMPTS,
) -> Callable[..., Any]:
    """Decorator: retry con backoff esponenziale (1s, 2s, 4s, 8s) su errori transitori.

    Rilancia sempre l'ultima eccezione (`reraise=True`): il fallimento finale non viene
    mai nascosto dietro un valore fittizio.
    """
    return tenacity_retry(
        reraise=True,
        retry=retry_if_exception_type(exception_types),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=15),
    )

from src.types.error import ErrorDetail


class AppError(Exception):
    """Errore di dominio: sa cosa e' successo, non sa in che formato HTTP viaggia.

    Il servizio solleva questo, l'handler lo traduce in risposta. Perche' un servizio
    che alza `HTTPException` smette di essere chiamabile da uno script, da un job, da
    un test: si lega al protocollo HTTP per poter essere riusato.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retry_after: int | None = None,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.details = details or []
        super().__init__(message)


class ChatSessionNotFoundError(AppError):
    def __init__(self, session_id: str) -> None:
        super().__init__(404, "CHAT_SESSION_NOT_FOUND", f"Sessione {session_id} non trovata")


class AccountNotFoundError(AppError):
    def __init__(self, account_id: str) -> None:
        super().__init__(404, "ACCOUNT_NOT_FOUND", f"Conto {account_id} non trovato")


class RateLimitError(AppError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            429,
            "RATE_LIMIT",
            f"Limite raggiunto. Riprova in {retry_after_seconds}s",
            retry_after=retry_after_seconds,
        )


class LLMProviderError(AppError):
    def __init__(self, provider: str, original: str) -> None:
        super().__init__(502, "LLM_PROVIDER_ERROR", f"Errore provider {provider}")
        self.original = original


class ImportFileError(AppError):
    """Il file non e' utilizzabile: non e' un errore del client da mascherare con un 500."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        super().__init__(status_code, code, message, details=details)


class IdempotencyKeyError(AppError):
    """L'header `Idempotency-Key` c'e' ma non e' una chiave: vuota, o troppo lunga."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            400,
            "INVALID_IDEMPOTENCY_KEY",
            f"Idempotency-Key non valida: {reason}",
        )


class IdempotencyConflictError(AppError):
    """Stessa chiave, contenuto diverso: non e' un rilancio, e' un file nuovo.

    Non lo si tratta come duplicato perche' restituire il conto di un file diverso
    sarebbe peggio che fallire: lo sportello vedrebbe un totale che non e' il suo e
    non avrebbe modo di accorgersene.
    """

    def __init__(self, key: str, first_hash: str, second_hash: str) -> None:
        super().__init__(
            409,
            "IDEMPOTENCY_KEY_CONFLICT",
            f"Idempotency-Key '{key}' gia' usata con un file diverso: "
            "se e' un file nuovo usa una chiave nuova",
            details=[
                ErrorDetail(
                    field="Idempotency-Key",
                    code="IDEMPOTENCY_KEY_CONFLICT",
                    message="la stessa chiave non puo' descrivere due contenuti diversi",
                    expected="stesso contenuto della prima richiesta",
                    received=f"prima {first_hash[:12]}, ora {second_hash[:12]}",
                )
            ],
        )

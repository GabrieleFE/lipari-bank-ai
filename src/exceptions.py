class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retry_after: int | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after = retry_after
        super().__init__(message)


class ChatSessionNotFoundError(AppError):
    def __init__(self, session_id: str) -> None:
        super().__init__(404, "CHAT_SESSION_NOT_FOUND", f"Sessione {session_id} non trovata")


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

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(status_code, code, message)

from datetime import UTC, datetime
from uuid import UUID

from src.db.repos import ChatRepository
from src.exceptions import ChatSessionNotFoundError
from src.types.chat import ChatResponse


class ChatService:
    """Business logic della chat: gestisce la sessione e persiste la conversazione."""

    def __init__(self, repo: ChatRepository) -> None:
        self._repo = repo

    async def send_message(self, session_id: str, message: str) -> ChatResponse:
        resolved: UUID | None = await self._resolve_session_id(session_id)
        if resolved is None:
            raise ChatSessionNotFoundError(session_id)

        existing = await self._repo.find_session(session_id=resolved)
        if existing is None:
            raise ChatSessionNotFoundError(str(resolved))

        # 1. Persist messaggio utente
        await self._repo.add_message(session_id=resolved, role="user", content=message)

        # 2. Risposta (echo fino al G4, poi LLM reale)
        reply_text = f"Echo: {message}"
        await self._repo.add_message(session_id=resolved, role="assistant", content=reply_text)

        return ChatResponse(
            session_id=str(resolved),
            reply=reply_text,
            tokens_used=10,
            cost_eur=0.0001,
            model_used="dummy",
            created_at=datetime.now(UTC),
        )

    async def _resolve_session_id(self, session_id: str) -> UUID | None:
        """Risolve l'id: 'new' crea una sessione; un UUID valido prosegue la conversazione."""
        if session_id == "new":
            return (await self._repo.create_session()).id
        return self._parse_uuid(session_id)

    @staticmethod
    def _parse_uuid(value: str) -> UUID | None:
        try:
            return UUID(value)
        except ValueError:
            return None

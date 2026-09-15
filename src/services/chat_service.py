from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from uuid import UUID

from src.db.models import ChatMessage
from src.db.repos import ChatRepository
from src.exceptions import ChatSessionNotFoundError
from src.llm.client import LLMProvider, Message
from src.llm.prompts import load_prompt
from src.observability.cost_tracker import CostTracker
from src.types.chat import ChatResponse


class ChatService:
    """Business logic della chat: sessione, history multi-turn, LLM e contabilita'."""

    def __init__(
        self,
        repo: ChatRepository,
        provider: LLMProvider,
        cost_tracker: CostTracker,
        *,
        history_max_messages: int = 20,
        max_tokens: int = 2000,
        system_prompt: str | None = None,
    ) -> None:
        self._repo = repo
        self._provider = provider
        self._cost_tracker = cost_tracker
        self._history_max_messages = history_max_messages
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt or load_prompt("chat_system_v1")

    async def send_message(self, session_id: str, message: str) -> ChatResponse:
        resolved = await self._resolve_session(session_id)
        await self._cost_tracker.check_budget()

        await self._repo.add_message(session_id=resolved, role="user", content=message)
        history = await self._repo.list_messages(session_id=resolved)

        response = await self._provider.complete(
            self._build_messages(history),
            max_tokens=self._max_tokens,
        )

        await self._repo.add_message(
            session_id=resolved,
            role="assistant",
            content=response.content,
            tokens=response.tokens_used,
            cost_eur=response.cost_eur,
            model_used=response.model,
        )

        return ChatResponse(
            session_id=str(resolved),
            reply=response.content,
            tokens_used=response.tokens_used,
            cost_eur=response.cost_eur,
            model_used=response.model,
            created_at=datetime.now(UTC),
        )

    async def chat_stream(self, session_id: str, message: str) -> AsyncIterator[str]:
        """Streaming: yielda i pezzi mano a mano e li accumula per salvare il messaggio
        intero a fine stream (una scrittura, non centinaia)."""
        resolved = await self._resolve_session(session_id)
        await self._cost_tracker.check_budget()

        await self._repo.add_message(session_id=resolved, role="user", content=message)
        history = await self._repo.list_messages(session_id=resolved)

        parts: list[str] = []
        tokens_used = 0
        cost_eur = 0.0
        model_used = ""
        async for chunk in self._provider.complete_stream(
            self._build_messages(history), max_tokens=self._max_tokens
        ):
            if chunk.text:
                parts.append(chunk.text)
                yield chunk.text
            if chunk.tokens_used:
                tokens_used = chunk.tokens_used
                cost_eur = chunk.cost_eur
                model_used = chunk.model

        await self._repo.add_message(
            session_id=resolved,
            role="assistant",
            content="".join(parts),
            tokens=tokens_used,
            cost_eur=cost_eur,
            model_used=model_used or self._provider.model,
        )

    def _build_messages(self, history: Sequence[ChatMessage]) -> list[Message]:
        # History troncata: gli ultimi N messaggi + system prompt (pattern "keep N recent + system")
        messages = [Message(role="system", content=self._system_prompt)]
        for msg in history[-self._history_max_messages :]:
            if msg.role == "user":
                messages.append(Message(role="user", content=msg.content))
            elif msg.role == "assistant":
                messages.append(Message(role="assistant", content=msg.content))
        return messages

    async def _resolve_session(self, session_id: str) -> UUID:
        resolved: UUID | None = await self._resolve_session_id(session_id)
        if resolved is None:
            raise ChatSessionNotFoundError(session_id)
        existing = await self._repo.find_session(session_id=resolved)
        if existing is None:
            raise ChatSessionNotFoundError(str(resolved))
        return resolved

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

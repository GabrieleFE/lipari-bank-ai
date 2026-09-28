from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ChatMessage
from src.db.repos import ChatRepository
from src.exceptions import ChatSessionNotFoundError
from src.llm.client import LLMProvider, Message
from src.llm.prompts import load_prompt
from src.observability.cost_tracker import CostTracker
from src.types.chat import ChatResponse


class ChatService:
    """Business logic della chat: sessione, history multi-turn, LLM e contabilita'.

    Riceve la sessione perche' la transazione e' sua: `add_message` scrive, ma non
    decide se la richiesta e' finita. Quel `commit()` e' qui, e in un solo punto.
    """

    def __init__(
        self,
        repo: ChatRepository,
        provider: LLMProvider,
        cost_tracker: CostTracker,
        session: AsyncSession,
        *,
        history_max_messages: int = 20,
        max_tokens: int = 2000,
        system_prompt: str | None = None,
    ) -> None:
        self._repo = repo
        self._provider = provider
        self._cost_tracker = cost_tracker
        self._session = session
        self._history_max_messages = history_max_messages
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt or load_prompt("chat_system_v1")

    async def send_message(self, session_id: str, message: str) -> ChatResponse:
        """Un turno di chat = una transazione, non due.

        Prima del Giorno 3 la richiesta scriveva due messaggi e ogni scrittura
        committava da sola, dal repository. Il risultato era una domanda salvata
        con dentro una risposta che non e' mai arrivata, se la chiamata all'LLM
        falliva: la conversatione si chiudeva a meta' e nessuno la vedeva, perche'
        il rollback non aveva niente da disfare.

        Adesso le due scritture restano aperte finche' non e' arrivata anche la
        risposta, e il `commit()` e' uno solo, qui sotto. Se l'LLM solleva, la
        `except` annulla tutto: la domanda non entra nel database.

        Anche `_resolve_session` e il controllo di budget stanno dentro il
        `try`, e non per simmetria: `_resolve_session("new")` ha gia' scritto e
        messo in flush la riga di sessione quando `check_budget` solleva il
        `429`. Fuori dal `try`, quella riga restava appesa alla transazione e
        veniva ripulita solo se qualcun altro si ricordava di farlo. Un servizio
        che gestisce la transazione deve sapere di aver aperto una scrittura
        anche quando non arriva a scrivere i messaggi.
        """
        try:
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
        except Exception:
            await self._session.rollback()
            raise

        await self._session.commit()

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
        intero a fine stream (una scrittura, non centinaia).

        Il `commit()` e' dentro il generatore e non dopo, perche' dopo il generatore
        finisce: e' l'ultima cosa che accade prima che lo stream si chiuda. Se il
        client si disconnette a meta', il generatore viene chiuso senza committare e
        `get_db` annulla quello che era stato scritto, domanda compresa. E' il
        comportamento giusto per la regola "tutto o niente", ed e' una scelta: si
        potrebbe voler salvare la domanda dell'utente anche se la risposta non
        arriva, e allora servirebbe un commit prima dello stream, con il rischio
        di avere domande senza risposta. Oggi si sceglie la coerenza.
        """
        resolved = await self._resolve_session(session_id)
        await self._cost_tracker.check_budget()

        await self._repo.add_message(session_id=resolved, role="user", content=message)
        history = await self._repo.list_messages(session_id=resolved)

        parts: list[str] = []
        try:
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
        except Exception:
            await self._session.rollback()
            raise

        await self._session.commit()

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

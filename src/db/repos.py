from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import ChatMessage, ChatSession


class ChatRepository:
    """Accesso al DB isolato: la via API per leggere/scrivere conversazioni."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_session(self, user_id: UUID | None = None) -> ChatSession:
        session = ChatSession(id=uuid4(), user_id=user_id)
        self._session.add(session)
        await self._session.commit()
        await self._session.refresh(session)
        return session

    async def find_session(self, session_id: UUID) -> ChatSession | None:
        stmt = (
            select(ChatSession)
            .where(ChatSession.id == session_id)
            .options(selectinload(ChatSession.messages))
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def add_message(
        self,
        *,
        session_id: UUID,
        role: str,
        content: str,
        tokens: int = 0,
        cost_eur: float = 0.0,
        model_used: str = "dummy",
    ) -> ChatMessage:
        message = ChatMessage(
            id=uuid4(),
            session_id=session_id,
            role=role,
            content=content,
            tokens=tokens,
            cost_eur=cost_eur,
            model_used=model_used,
            created_at=datetime.now(UTC),
        )
        self._session.add(message)
        await self._session.commit()
        await self._session.refresh(message)
        return message

    async def list_messages(self, session_id: UUID) -> Sequence[ChatMessage]:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def count_user_sessions(self, user_id: UUID) -> int:
        stmt = select(ChatSession.id).where(ChatSession.user_id == user_id)
        return len((await self._session.execute(stmt)).scalars().all())

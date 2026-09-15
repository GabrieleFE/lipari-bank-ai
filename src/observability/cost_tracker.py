from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ChatMessage
from src.exceptions import RateLimitError

BUDGET_EXCEEDED_RETRY_SECONDS = 3600


class CostTracker:
    """Somma il costo dei messaggi della giornata e ferma la spesa oltre la soglia.

    Il guardrail e' una somma sul dato gia' persistito al Giorno 3 (`cost_eur`):
    nessun progetto aggiuntivo, solo lettura + confronto con la soglia.
    """

    def __init__(self, session: AsyncSession, max_eur_per_day: float) -> None:
        self._session = session
        self.max_eur_per_day = max_eur_per_day

    async def daily_cost(self, target_date: date | None = None) -> float:
        # default None nella firma: valutato dentro la funzione, non all'import
        day = target_date or datetime.now().date()
        stmt = select(func.sum(ChatMessage.cost_eur)).where(
            func.date(ChatMessage.created_at) == day
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() or 0.0

    async def check_budget(self) -> None:
        if await self.daily_cost() >= self.max_eur_per_day:
            raise RateLimitError(retry_after_seconds=BUDGET_EXCEEDED_RETRY_SECONDS)

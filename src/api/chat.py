import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.repos import ChatRepository
from src.db.session import get_db
from src.llm.client import LLMProvider
from src.llm.factory import get_llm_provider
from src.observability.cost_tracker import CostTracker
from src.services.chat_service import ChatService
from src.types.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/ai", tags=["Chat"])


def get_chat_service(
    session: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(get_llm_provider),
) -> ChatService:
    return ChatService(
        repo=ChatRepository(session),
        provider=provider,
        cost_tracker=CostTracker(session, settings.max_daily_cost_eur),
        history_max_messages=settings.history_max_messages,
        max_tokens=settings.max_tokens_per_request,
    )


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Send message to AI assistant",
    description=(
        "Multi-turn conversation persisted su PostgreSQL. "
        "'new' crea una sessione, un UUID esistente continua la conversazione."
    ),
    responses={
        200: {
            "description": "Reply ok",
            "content": {
                "application/json": {
                    "example": {
                        "session_id": "3f9c1b2e-0000-4000-8000-000000000001",
                        "reply": "Buongiorno! Come posso aiutarla?",
                        "tool_calls": [],
                        "tokens_used": 150,
                        "cost_eur": 0.00005,
                        "model_used": "gpt-4o-mini",
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                }
            },
        },
        404: {"description": "Session not found"},
        422: {"description": "Validation"},
        429: {"description": "Rate limit / budget esaurito"},
    },
)
async def chat(
    req: ChatRequest,
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    return await service.send_message(session_id=req.session_id, message=req.message)


@router.post("/chat/stream", summary="Streaming chat via Server-Sent Events")
async def chat_stream(
    req: ChatRequest,
    service: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    async def event_generator() -> AsyncGenerator[str, None]:
        async for chunk in service.chat_stream(req.session_id, req.message):
            payload = json.dumps({"delta": chunk}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repos import ChatRepository
from src.db.session import get_db
from src.services.chat_service import ChatService
from src.types.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/ai", tags=["Chat"])


def get_chat_service(session: AsyncSession = Depends(get_db)) -> ChatService:
    return ChatService(ChatRepository(session))


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
                        "reply": "Echo: Ciao!",
                        "tool_calls": [],
                        "tokens_used": 10,
                        "cost_eur": 0.0001,
                        "model_used": "dummy",
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                }
            },
        },
        404: {"description": "Session not found"},
        422: {"description": "Validation"},
        429: {"description": "Rate limit"},
    },
)
async def chat(
    req: ChatRequest,
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    return await service.send_message(session_id=req.session_id, message=req.message)

from datetime import UTC, datetime

from fastapi import APIRouter

from src.types.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/ai", tags=["Chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Send message to AI assistant",
    description="Multi-turn conversation. In G4 collegheremo LLM reale.",
    responses={
        200: {
            "description": "Reply ok",
            "content": {
                "application/json": {
                    "example": {
                        "session_id": "s-123",
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
        422: {"description": "Validation"},
        429: {"description": "Rate limit"},
    },
)
async def chat(req: ChatRequest) -> ChatResponse:
    return ChatResponse(
        session_id=req.session_id,
        reply=f"Echo: {req.message}",
        tokens_used=10,
        cost_eur=0.0001,
        model_used="dummy",
        created_at=datetime.now(UTC),
    )

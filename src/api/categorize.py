from typing import Any

from fastapi import APIRouter, Depends

from src.config import settings
from src.services.categorize_service import CategorizeService
from src.types.categorize import CategorizeRequest, CategorizeResponse

router = APIRouter(prefix="/api/ai", tags=["Categorize"])

_instructor_client: Any = None  # singleton lazily created


def get_categorize_service() -> CategorizeService:
    """Singleton: Instructor wrappa AsyncOpenAI e forza lo structured output.
    Nei test si sostituisce con un fake tramite `app.dependency_overrides`."""
    global _instructor_client
    if _instructor_client is None:
        import instructor
        from openai import AsyncOpenAI

        _instructor_client = instructor.from_openai(
            AsyncOpenAI(
                api_key=settings.openai_api_key,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
            )
        )
    return CategorizeService(_instructor_client, model=settings.categorize_model)


@router.post(
    "/categorize",
    response_model=CategorizeResponse,
    summary="Categorize transaction via LLM",
    description=(
        "Categorizza una transazione bancaria con structured output garantito da Instructor."
    ),
)
async def categorize(
    req: CategorizeRequest,
    service: CategorizeService = Depends(get_categorize_service),
) -> CategorizeResponse:
    return await service.categorize(req)

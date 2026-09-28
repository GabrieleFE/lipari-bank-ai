from typing import Protocol, runtime_checkable

from fastapi import APIRouter, Depends

from src.config import require_secret, settings
from src.services.categorize_rules import KeywordCategorizeService
from src.services.categorize_service import CategorizeService
from src.types.categorize import CategorizeRequest, CategorizeResponse
from src.types.error import ErrorResponse

router = APIRouter(prefix="/api/ai", tags=["Categorize"])

_instructor_client: object | None = None  # singleton lazily created


@runtime_checkable
class Categorizer(Protocol):
    """Cosa deve saper fare chi classifica un movimento.

    Un Protocol, non una classe base: la regola a parole chiave e il provider LLM non
    hanno niente in comune tranne questo metodo, ed e' questo metodo a essere il
    contratto che l'endpoint promette al chiamante.
    """

    async def categorize(self, req: CategorizeRequest) -> CategorizeResponse: ...


def build_categorizer(provider: str | None = None, model: str | None = None) -> Categorizer:
    """Il punto in cui si sceglie *chi* classifica. Il contratto non cambia.

    `rules` non richiede chiavi ne' rete: e' il default perche' l'endpoint deve
    rispondere anche quando non c'e' un modello. `llm` e' il percorso che si attiva
    quando il modello arriva, senza toccare router, modelli o test.
    """
    global _instructor_client
    chosen = provider or settings.categorize_provider
    if chosen == "rules":
        return KeywordCategorizeService()
    if _instructor_client is None:
        import instructor
        from openai import AsyncOpenAI

        _instructor_client = instructor.from_openai(
            AsyncOpenAI(
                api_key=require_secret(settings.openai_api_key, "OPENAI_API_KEY"),
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
            )
        )
    return CategorizeService(_instructor_client, model=model or settings.categorize_model)


def get_categorize_service() -> Categorizer:
    """La dipendenza che l'endpoint riceve, senza parametri.

    Senza argomenti di proposito: FastAPI legge la firma della dipendenza e avrebbe
    pubblicato ogni parametro come query parameter dell'endpoint, inventando
    `?model=` e `?provider=` che non esistono nel contratto.
    """
    return build_categorizer()


@router.post(
    "/categorize",
    response_model=CategorizeResponse,
    summary="Classifica un movimento e dichiara quanto e' sicuro",
    description=(
        "Classifica la descrizione di un movimento in una delle sei categorie e "
        "restituisce la confidenza della classificazione. Il default e' la regola a "
        "parole chiave: la stessa risposta arriva dal provider LLM quando e' "
        "configurato, perche' il contratto e' quello, non il modello."
    ),
    responses={
        200: {"model": CategorizeResponse, "description": "Categoria e confidenza"},
        422: {"model": ErrorResponse, "description": "Richiesta malformata: vedi `details`"},
    },
)
async def categorize(
    req: CategorizeRequest,
    service: Categorizer = Depends(get_categorize_service),
) -> CategorizeResponse:
    return await service.categorize(req)

"""Categorizzazione transazioni via LLM + Instructor (structured output)."""

from typing import Any, cast

from src.llm.prompts import load_prompt
from src.types.categorize import CategorizeRequest, CategorizeResponse


class CategorizeService:
    """Instructor forza il LLM a restituire un JSON conforme a CategorizeResponse.

    Lo schema Pydantic viene iniettato nella prompt; se il modello restituisce un formato
    non valido, l'errore di validazione viene rimandato al modello (max_retries=2).
    """

    def __init__(self, client: Any, model: str) -> None:  # noqa: ANN401
        # Il client Instructor e' un wrapper tipizzato in modo complesso e version-specific:
        # accettiamo Any per non legare l'abstraction a una versione di instructor.
        self._client = client
        self._model = model

    async def categorize(self, req: CategorizeRequest) -> CategorizeResponse:
        user_content = f"Description: {req.description}\nAmount: {req.amount} {req.currency}"
        result = await self._client.chat.completions.create(
            model=self._model,
            response_model=CategorizeResponse,
            messages=[
                {
                    "role": "system",
                    "content": load_prompt("categorize_system_v1"),
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            max_retries=2,
            temperature=0.0,  # deterministic
        )
        return cast(CategorizeResponse, result)

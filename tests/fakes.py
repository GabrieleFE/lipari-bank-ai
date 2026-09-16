"""Fakes per i test: risposte deterministiche senza chiamare API esterne."""

import hashlib
from collections.abc import AsyncIterator

from src.config import settings
from src.llm.client import Message, StreamChunk
from src.types.categorize import CategorizeRequest, CategorizeResponse


class FakeLLMResponse:
    def __init__(self, content: str, tokens_used: int, cost_eur: float, model: str) -> None:
        self.content = content
        self.tokens_used = tokens_used
        self.cost_eur = cost_eur
        self.model = model


class FakeLLMProvider:
    """Provider fittizio con risposte deterministiche; registra le chiamate ricevute."""

    model: str = "fake-gpt"

    def __init__(self, response_text: str = "Fake reply") -> None:
        self.response_text = response_text
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, messages: list[Message], max_tokens: int = 500) -> FakeLLMResponse:
        self.calls.append([m.model_dump() for m in messages])
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return FakeLLMResponse(
            content=f"{self.response_text}: {last_user}",
            tokens_used=10,
            cost_eur=0.0001,
            model=self.model,
        )

    async def complete_stream(
        self, messages: list[Message], max_tokens: int = 500
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append([m.model_dump() for m in messages])
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        full = f"{self.response_text}: {last_user}"
        for i in range(0, len(full), 5):
            yield StreamChunk(text=full[i : i + 5])
        yield StreamChunk(text="", tokens_used=10, cost_eur=0.0001, model=self.model)


class FakeEmbeddingClient:
    """Embedding deterministico della giusta dimensione, senza rete.

    Stesso testo -> stesso vettore (hash SHA-256). Le similarita' risultanti
    non sono semantiche: servono a testare la pipeline (inserimento, query
    SQL su pgvector, ordine), non la qualita' del retrieval.
    """

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embedding_dim
        self.calls: list[list[str]] = []

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vector = [(float(b) / 127.5) - 1.0 for b in digest]
        vector = vector * ((self.dim // len(vector)) + 1)
        return vector[: self.dim]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [self._vector(t) for t in texts]

    async def embed_one(self, text: str) -> list[float]:
        return self._vector(text)


class FakeCategorizeService:
    """Categorizer deterministico basato su keyword: preserva le asserzioni dei test."""

    async def categorize(self, req: CategorizeRequest) -> CategorizeResponse:
        desc = req.description.lower()
        if any(k in desc for k in ("enel", "bolletta", "luce", "gas")):
            return CategorizeResponse(
                category="UTILITIES",
                subcategory="ENERGY",
                confidence=0.92,
                reasoning="Description contains utility keywords",
            )
        if any(k in desc for k in ("supermercato", "esselunga", "coop")):
            return CategorizeResponse(
                category="GROCERIES",
                subcategory="SUPERMARKET",
                confidence=0.85,
                reasoning="Description matches grocery store",
            )
        return CategorizeResponse(
            category="OTHER",
            subcategory="UNCATEGORIZED",
            confidence=0.10,
            reasoning="No matching pattern",
        )

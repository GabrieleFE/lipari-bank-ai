"""Client embedding locale basato su sentence-transformers (Giorno 5).

Scelta (sliding door B): embedding self-hosted invece dell'API OpenAI.
Motivazione v1 LipariBank: nessuna chiave API disponibile per gli embedding e,
per una banca, l'argomento decisivo è che i dati non escono dall'azienda.

Vincolo non violabile: documenti e query devono usare lo STESSO modello.
Il modello è la configurazione `EMBEDDING_MODEL`; la dimensione del vettore
(`EMBEDDING_DIM`, 384 per `paraphrase-multilingual-MiniLM-L12-v2`) deve
coincidere con il `Vector(...)` della migration — cambiare modello implica
una migration.
"""

import asyncio
from typing import TYPE_CHECKING, ClassVar, Protocol

from src.config import settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class EmbeddingClientProtocol(Protocol):
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]: ...


class EmbeddingClient:
    """Il metodo primario è `embed` (batch), non `embed_one`.

    L'encode di sentence-transformers è CPU-bound: gira su un thread
    (to_thread) per non bloccare l'event loop di FastAPI.
    """

    _model: ClassVar["SentenceTransformer | None"] = None

    def __init__(self) -> None:
        self.model_name = settings.embedding_model
        self.dim = settings.embedding_dim

    @classmethod
    def _get_model(cls) -> "SentenceTransformer":
        if cls._model is None:
            from sentence_transformers import SentenceTransformer

            cls._model = SentenceTransformer(settings.embedding_model)
        return cls._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Batch embed multiple texts. L'ordine è garantito: data[i] == texts[i]."""
        if not texts:
            return []
        model = self._get_model()
        vectors = await asyncio.to_thread(lambda: model.encode(texts, normalize_embeddings=True))
        return [v.tolist() for v in vectors]

    async def embed_one(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0]

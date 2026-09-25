from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.llm.embedding_client import EmbeddingClientProtocol


class RetrievalResult(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    similarity: float
    metadata: dict[str, object]


class RetrievalService:
    def __init__(self, session: AsyncSession, embedding_client: EmbeddingClientProtocol) -> None:
        self.session = session
        self.embedding_client = embedding_client

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: float | None = None,
    ) -> list[RetrievalResult]:
        # La domanda va vettorizzata con lo STESSO modello dei documenti:
        # vettori di modelli diversi vivono in spazi diversi.
        query_embedding = await self.embedding_client.embed_one(query)

        # SQL raw: gli operatori di pgvector non hanno equivalente nel builder.
        # Parametri sempre legati (:query_emb, :top_k), mai interpolati.
        stmt_text = """
            SELECT id, document_id, content, chunk_metadata,
                   1 - (embedding <=> :query_emb) AS similarity
            FROM document_chunks
        """
        params: dict[str, object] = {"query_emb": str(query_embedding), "top_k": top_k}
        if min_similarity is not None:
            stmt_text += " WHERE 1 - (embedding <=> :query_emb) >= :min_similarity"
            params["min_similarity"] = min_similarity
        stmt_text += """
            ORDER BY embedding <=> :query_emb
            LIMIT :top_k
        """

        result = await self.session.execute(text(stmt_text), params)
        rows = result.fetchall()

        return [
            RetrievalResult(
                chunk_id=row.id,
                document_id=row.document_id,
                content=row.content,
                similarity=round(row.similarity, 4),
                metadata=row.chunk_metadata or {},
            )
            for row in rows
        ]

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import DocumentChunk
from src.lib.chunking import chunk_text
from src.llm.embedding_client import EmbeddingClientProtocol


class IngestService:
    def __init__(self, session: AsyncSession, embedding_client: EmbeddingClientProtocol) -> None:
        self.session = session
        self.embedding_client = embedding_client

    async def ingest_document(
        self,
        document_id: str,
        content: str,
        metadata: dict[str, object] | None = None,
    ) -> int:
        # Re-ingest idempotente: elimina i chunk esistenti per questo documento
        # PRIMA di inserire, nella stessa transazione. Ingerire due volte lo stesso
        # documento senza questo passo genera chunk duplicati che occupano i primi
        # posti del retrieval.
        await self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )

        chunks = chunk_text(content, chunk_size=500, overlap=50)
        embeddings = await self.embedding_client.embed(chunks)

        for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            db_chunk = DocumentChunk(
                document_id=document_id,
                chunk_index=idx,
                content=chunk,
                embedding=embedding,
                chunk_metadata=metadata or {},
            )
            self.session.add(db_chunk)

        await self.session.commit()
        return len(chunks)

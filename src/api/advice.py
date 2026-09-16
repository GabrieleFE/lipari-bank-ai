from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.llm.client import LLMProvider
from src.llm.embedding_client import EmbeddingClient
from src.llm.factory import get_llm_provider
from src.services.ingest_service import IngestService
from src.services.rag_service import RAGService
from src.services.retrieval_service import RetrievalService
from src.types.advice import AdviceRequest, AdviceResponse, IngestRequest, IngestResponse

router = APIRouter(prefix="/api/ai", tags=["Advice"])


def get_embedding_client() -> EmbeddingClient:
    return EmbeddingClient()


def get_retrieval_service(
    session: AsyncSession = Depends(get_db),
    embedding_client: EmbeddingClient = Depends(get_embedding_client),
) -> RetrievalService:
    return RetrievalService(session=session, embedding_client=embedding_client)


def get_rag_service(
    retrieval: RetrievalService = Depends(get_retrieval_service),
    llm: LLMProvider = Depends(get_llm_provider),
) -> RAGService:
    return RAGService(retrieval=retrieval, llm=llm)


def get_ingest_service(
    session: AsyncSession = Depends(get_db),
    embedding_client: EmbeddingClient = Depends(get_embedding_client),
) -> IngestService:
    return IngestService(session=session, embedding_client=embedding_client)


@router.post("/advice", response_model=AdviceResponse)
async def advice(
    req: AdviceRequest, service: RAGService = Depends(get_rag_service)
) -> AdviceResponse:
    return await service.answer(req)


@router.post("/documents/ingest", response_model=IngestResponse)
async def ingest(
    req: IngestRequest,
    service: IngestService = Depends(get_ingest_service),
    embedding_client: EmbeddingClient = Depends(get_embedding_client),
) -> IngestResponse:
    count = await service.ingest_document(req.document_id, req.content, req.metadata)
    return IngestResponse(chunk_count=count, embedding_dim=embedding_client.dim)

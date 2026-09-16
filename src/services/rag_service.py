from src.llm.client import LLMProvider, Message
from src.llm.prompts import load_prompt
from src.services.retrieval_service import RetrievalService
from src.types.advice import AdviceRequest, AdviceResponse, Citation


class RAGService:
    """Retrieve top-k, costruisce il contesto, genera con citation.

    Nota (giorno 5): le citations sono i chunk RECUPERATI, non quelli che il
    modello ha davvero usato: e' una lista di fonti consultate. Le citazioni
    credibili si estraggono dai marcatori [doc_id: X] scritti dal modello.
    """

    # Soglia di default: i chunk sotto questo valore di similarita' NON entrano
    # nel prompt (difesa dalle domande fuori dominio). Il valore corretto si
    # misura sulla distribuzione dei propri documenti (confronto Giorno 6):
    # con paraphrase-multilingual-MiniLM-L12-v2 misuriamo in dominio 0.50-0.72
    # e fuori dominio 0.31-0.41, quindi 0.45 separa i due gruppi.
    MIN_SIMILARITY = 0.45

    def __init__(self, retrieval: RetrievalService, llm: LLMProvider) -> None:
        self.retrieval = retrieval
        self.llm = llm
        self.system_prompt = load_prompt("advice_system_v1")

    async def answer(self, req: AdviceRequest) -> AdviceResponse:
        chunks = await self.retrieval.retrieve(
            req.question,
            top_k=5,
            min_similarity=self.MIN_SIMILARITY,
        )

        if not chunks:
            return AdviceResponse(
                answer="Non ho informazioni su questa domanda.",
                citations=[],
                tokens_used=0,
                cost_eur=0,
            )

        context_parts = [
            (
                f"[doc_id: {c.document_id}, chunk: {c.chunk_id}, "
                f"similarity: {c.similarity:.2f}]\n{c.content}"
            )
            for c in chunks
        ]
        context = "\n\n---\n\n".join(context_parts)

        user_prompt = f"""CONTESTI:
{context}

DOMANDA: {req.question}

RISPOSTA (con citazioni):"""

        llm_response = await self.llm.complete(
            messages=[
                Message(role="system", content=self.system_prompt),
                Message(role="user", content=user_prompt),
            ],
            max_tokens=800,
        )

        citations = [
            Citation(
                document_id=c.document_id,
                chunk_id=c.chunk_id,
                excerpt=c.content[:200] + "..." if len(c.content) > 200 else c.content,
                similarity=c.similarity,
            )
            for c in chunks
        ]

        return AdviceResponse(
            answer=llm_response.content,
            citations=citations,
            tokens_used=llm_response.tokens_used,
            cost_eur=llm_response.cost_eur,
        )

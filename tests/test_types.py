from datetime import UTC, datetime

from pydantic import ValidationError

from src.types.advice import (
    AdviceRequest,
    AdviceResponse,
    Citation,
    IngestRequest,
    IngestResponse,
)
from src.types.categorize import CategorizeRequest, CategorizeResponse
from src.types.chat import ChatRequest, ChatResponse, ToolCallInfo
from src.types.error import ErrorResponse


def test_chat_request_requires_non_empty_message() -> None:
    try:
        ChatRequest(session_id="s-1", message="")
        raise AssertionError("Should have raised")
    except ValidationError as e:
        assert "message" in str(e)


def test_chat_request_message_over_2000_rejected() -> None:
    try:
        ChatRequest(session_id="s-1", message="a" * 2001)
        raise AssertionError("Should have raised")
    except ValidationError:
        pass


def test_chat_response_tool_calls_default_empty() -> None:
    response = ChatResponse(
        session_id="s-1",
        reply="ok",
        tokens_used=1,
        cost_eur=0.0,
        model_used="dummy",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert response.tool_calls == []


def test_chat_response_with_tool_calls() -> None:
    response = ChatResponse(
        session_id="s-1",
        reply="ok",
        tool_calls=[ToolCallInfo(name="check_balance", arguments={"acc": "1"})],
        tokens_used=1,
        cost_eur=0.0,
        model_used="dummy",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert response.tool_calls[0].name == "check_balance"
    assert response.tool_calls[0].result is None


def test_chat_response_rejects_negative_tokens() -> None:
    try:
        ChatResponse(
            session_id="s-1",
            reply="ok",
            tokens_used=-1,
            cost_eur=0.0,
            model_used="dummy",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        raise AssertionError("Should have raised")
    except ValidationError:
        pass


def test_categorize_request_requires_positive_amount() -> None:
    try:
        CategorizeRequest(description="ensa", amount=0)
        raise AssertionError("Should have raised")
    except ValidationError:
        pass


def test_categorize_request_default_currency() -> None:
    req = CategorizeRequest(description="ensa", amount=10)
    assert req.currency == "EUR"


def test_categorize_request_invalid_currency_rejected() -> None:
    try:
        CategorizeRequest(description="ensa", amount=10, currency="euro")
        raise AssertionError("Should have raised")
    except ValidationError:
        pass


def test_categorize_response_confidence_bounds() -> None:
    try:
        CategorizeResponse(
            category="OTHER",
            subcategory="X",
            confidence=1.5,
            reasoning="too high",
        )
        raise AssertionError("Should have raised")
    except ValidationError:
        pass


def test_categorize_response_invalid_category_rejected() -> None:
    try:
        CategorizeResponse.model_validate(
            {
                "category": "INVALID",
                "subcategory": "X",
                "confidence": 0.5,
                "reasoning": "bad",
            }
        )
        raise AssertionError("Should have raised")
    except ValidationError:
        pass


def test_error_response_optional_details() -> None:
    err = ErrorResponse(
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        status=422,
        error="VALIDATION_ERROR",
        message="Input non valido",
        path="/x",
    )
    assert err.details is None


def test_advice_request_short_question_rejected() -> None:
    try:
        AdviceRequest(question="ciao")
        raise AssertionError("Should have raised")
    except ValidationError:
        pass


def test_advice_request_blank_question_rejected() -> None:
    try:
        AdviceRequest(question="   ")
        raise AssertionError("Should have raised")
    except ValidationError:
        pass


def test_advice_response_with_citation() -> None:
    response = AdviceResponse(
        answer="Il bonifico SEPA istantaneo costa €1.00 [doc_id: commissioni_bonifico].",
        citations=[
            Citation(
                document_id="commissioni_bonifico",
                chunk_id="chunk-1",
                excerpt="Bonifico istantaneo SEPA: costo €1.00...",
                similarity=0.89,
            )
        ],
        tokens_used=245,
        cost_eur=0.0012,
    )
    assert response.citations[0].document_id == "commissioni_bonifico"
    assert response.citations[0].similarity == 0.89
    assert response.answer.startswith("Il bonifico")


def test_ingest_request_valid() -> None:
    req = IngestRequest(
        document_id="commissioni_bonifico",
        content="Le commissioni per il bonifico SEPA sono di 1 euro.",
        metadata={"source": "data/docs/commissioni_bonifico.md"},
    )
    assert req.metadata is not None
    assert req.metadata["source"] == "data/docs/commissioni_bonifico.md"


def test_ingest_request_too_short_content_rejected() -> None:
    try:
        IngestRequest(document_id="d-1", content="abc")
        raise AssertionError("Should have raised")
    except ValidationError:
        pass


def test_ingest_response_valid() -> None:
    resp = IngestResponse(chunk_count=12, embedding_dim=1536)
    assert resp.chunk_count == 12
    assert resp.embedding_dim == 1536

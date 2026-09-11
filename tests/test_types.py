from pydantic import ValidationError

from src.types.advice import AdviceRequest, AdviceResponse, Citation
from src.types.categorize import CategorizeRequest, CategorizeResponse
from src.types.chat import ChatRequest, ChatResponse, ToolCallInfo
from src.types.error import ErrorResponse
from src.types.ingest import DocumentIngestRequest


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
        created_at="2024-01-01T00:00:00Z",
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
        created_at="2024-01-01T00:00:00Z",
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
            created_at="2024-01-01T00:00:00Z",
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
        CategorizeResponse(
            category="INVALID",
            subcategory="X",
            confidence=0.5,
            reasoning="bad",
        )
        raise AssertionError("Should have raised")
    except ValidationError:
        pass


def test_error_response_optional_details() -> None:
    err = ErrorResponse(
        timestamp="2024-01-01T00:00:00Z",
        status=422,
        error="VALIDATION_ERROR",
        message="Input non valido",
        path="/x",
    )
    assert err.details is None


def test_advice_request_strips_whitespace() -> None:
    req = AdviceRequest(question="  Come investo?  ")
    assert req.question == "Come investo?"


def test_advice_request_blank_question_rejected() -> None:
    try:
        AdviceRequest(question="   ")
        raise AssertionError("Should have raised")
    except ValidationError:
        pass


def test_advice_response_with_citation() -> None:
    response = AdviceResponse(
        advice="Investi in obbligazioni",
        citations=[Citation(doc_id="d-1", title="Guida obbligazioni", excerpt="cap. 3", page=3)],
        tokens_used=50,
        cost_eur=0.001,
        model_used="dummy",
        created_at="2024-01-01T00:00:00Z",
    )
    assert response.citations[0].doc_id == "d-1"
    assert response.citations[0].page == 3


def test_ingest_blank_content_rejected() -> None:
    try:
        DocumentIngestRequest(document_id="d-1", title="Titolo", content="   ")
        raise AssertionError("Should have raised")
    except ValidationError:
        pass


def test_ingest_valid() -> None:
    doc = DocumentIngestRequest(
        document_id="d-1",
        title="Titolo",
        content="  Testo del documento  ",
        tags=["fisco"],
    )
    assert doc.content == "Testo del documento"
    assert doc.source is None

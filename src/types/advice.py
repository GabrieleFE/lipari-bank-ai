from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class Citation(BaseModel):
    doc_id: str = Field(..., description="ID del documento sorgente")
    title: str
    excerpt: str = Field(..., description="Frammento testuale citato")
    page: int | None = Field(default=None, ge=0)


class AdviceRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000, description="Domanda dell'utente")

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Question cannot be blank")
        return stripped


class AdviceResponse(BaseModel):
    advice: str
    citations: list[Citation] = Field(default_factory=list)
    tokens_used: int = Field(..., ge=0)
    cost_eur: float = Field(..., ge=0)
    model_used: str
    created_at: datetime

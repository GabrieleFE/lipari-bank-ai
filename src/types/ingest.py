from pydantic import BaseModel, Field, field_validator


class DocumentIngestRequest(BaseModel):
    document_id: str = Field(..., description="Identificativo esterno del documento")
    title: str = Field(..., min_length=1, max_length=300)
    content: str = Field(..., description="Testo integrale del documento")
    source: str | None = Field(default=None, max_length=100)
    tags: list[str] = Field(default_factory=list)

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Content cannot be blank")
        return stripped

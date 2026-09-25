from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class MovementRow(BaseModel):
    """Una riga dell'estratto conto: il contratto di validazione dell'import.

    Le stesse regole valgono per la singola chiamata `/categorize`: se una riga
    passa qui, la stessa descrizione e lo stesso importo passerebbero anche lì.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    date: date
    description: str = Field(..., min_length=1, max_length=200)
    amount: float = Field(..., gt=0)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")


class ImportProblem(BaseModel):
    row: int = Field(..., ge=1, description="Numero di riga nel file; la riga 1 e' l'intestazione")
    field: str | None = Field(
        default=None, description="Campo non valido, se il problema e' su un campo"
    )
    reason: str = Field(..., description="Cosa non va e quale valore era arrivato")


class MovementImportResponse(BaseModel):
    imported_count: int = Field(..., ge=0, description="Righe accettate dal contratto")
    problems: list[ImportProblem] = Field(
        default_factory=list, description="Una voce per ogni riga rifiutata, con il suo numero"
    )

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CategoryEnum = Literal[
    "UTILITIES", "GROCERIES", "TRANSPORT", "RESTAURANTS", "ENTERTAINMENT", "OTHER"
]

CATEGORIES: tuple[CategoryEnum, ...] = (
    "UTILITIES",
    "GROCERIES",
    "TRANSPORT",
    "RESTAURANTS",
    "ENTERTAINMENT",
    "OTHER",
)


class CategorizeRequest(BaseModel):
    """L'ingresso di `/categorize`.

    Due scelte che sembrano dettagli e non lo sono:

    * `str_strip_whitespace`: senza, `"   "` passa `min_length=1` e l'endpoint
      classifica una riga che lo sportello aveva scritto sbagliando lo spazio. Con,
      la riga torna indietro con `EMPTY` e il campo giusto.
    * `extra="forbid"`: senza, `{"descriptions": ...}` viene scartato in silenzio e
      l'endpoint classifica una descrizione che il chiamante non ha mai mandato. In
      un sistema che si impegna a non perdere pezzi dal conto, ignorare un campo
      sconosciuto e' la stessa perdita, solo piu' difficile da vedere.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    description: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="La causale come arriva dall'estratto conto, non riscritta",
    )
    amount: float = Field(..., gt=0, description="Importo del movimento, sempre positivo")
    currency: str = Field(
        default="EUR",
        pattern=r"^[A-Z]{3}$",
        description="Codice valuta ISO a tre lettere maiuscole; EUR se non dichiarato",
    )


class CategorizeResponse(BaseModel):
    category: CategoryEnum
    subcategory: str
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Quanto siamo sicuri della categoria, da 0 a 1. Sotto 0.5 la riga va "
            "riguardata: il contratto permette a chi riceve di decidere, e non solo "
            "di fidarsi."
        ),
    )
    reasoning: str = Field(..., description="Perche' questa categoria, in una frase")

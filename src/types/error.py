from datetime import datetime

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Un singolo problema, machine-readable.

    Un programma conta gli errori per `code` e li individua per `field`; una persona
    legge `message`. I due non litigano perche' `message` e' gia' in italiano: qui non
    si duplica il contratto, si dichiara cosa e' andato storto.
    """

    field: str = Field(..., description="Campo da correggere, es. 'amount' o 'file'")
    code: str = Field(
        ...,
        description="Codice stabile del problema, da usare per branchare o contare",
        examples=["NOT_POSITIVE", "NOT_A_DATE", "MISSING"],
    )
    message: str = Field(..., description="Cosa non va, in italiano")
    expected: str | None = Field(default=None, description="Contratto atteso, in forma breve")
    received: str | None = Field(
        default=None, description="Cosa c'era, troncato: serve a chi legge, non a un parser"
    )


class ErrorResponse(BaseModel):
    """L'unica busta di errore dell'API.

    `details` e' la lista dei problemi individuali, ognuno con la stessa forma sia
    che venga dal 422 generato da FastAPI sia da un errore di dominio. Chi riceve
    non deve distinguere i due casi: sono lo stesso problema con due cause diverse.
    """

    timestamp: datetime
    status: int
    error: str = Field(..., description="Codice dell'errore, una costante per tipo")
    message: str
    path: str
    details: list[ErrorDetail] = Field(
        default_factory=list,
        description=(
            "Problemi individuali. Sempre presente, anche vuota: il chiamante non "
            "deve distinguere 'nessun dettaglio' da 'dettaglio assente', perche' non "
            "c'e' differenza."
        ),
    )

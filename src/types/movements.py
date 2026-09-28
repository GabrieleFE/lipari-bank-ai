from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from src.types.error import ErrorDetail


class MovementRow(BaseModel):
    """Una riga dell'estratto conto: il contratto di validazione dell'import.

    Le stesse regole valgono per la singola chiamata `/categorize`: se una riga
    passa qui, la stessa descrizione e lo stesso importo passerebbero anche lì.
    I vincoli sono dichiarati nel campo (`gt=0`, `min_length=1`, `pattern`), non in
    una catena di `if` dentro l'endpoint: per questo appaiono nella documentazione
    OpenAPI e un cambio di regola non puo' dimenticare di aggiornare qualcosa.
    `extra="forbid"` perche' una colonna in piu' nell'intestazione non deve passare
    in silenzio: l'estratto di un sistema che sbaglia le intestazioni sbaglia anche
    i nomi delle colonne.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    date: date
    description: str = Field(..., min_length=1, max_length=200)
    amount: float = Field(..., gt=0)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")


class ImportProblem(BaseModel):
    """Una riga rifiutata, e il conto di dove viene.

    `row` e' il numero di riga nel file (la riga 1 e' l'intestazione), cosi' lo
    sportello apre il file alla riga giusta invece di contare a mano. `detail` ha
    la stessa forma del 422 di una richiesta malformata: stesso campo, stesso codice,
    stessa frase. `reason` e' la sua resa in una riga leggibile, non una seconda
    fonte di verita'.
    """

    row: int = Field(..., ge=1, description="Numero di riga nel file (la riga 1 e' l'intestazione)")
    reason: str = Field(..., description="Il problema in una frase leggibile")
    detail: ErrorDetail = Field(..., description="Il problema in forma machine-readable")


class MovementImportResponse(BaseModel):
    """Il conto dell'import: quante righe dentro, quante fuori, e perché.

    `imported_count + len(problems) == total_rows` e' la proprietà che l'ufficio
    controlla a occhio prima di fine mese. Esposta come campo invece che lasciata
    implicita, perché sia un operatore sia un programma possano verificarla senza
    ricontare il file.
    """

    imported_count: int = Field(..., ge=0, description="Righe accettate dal contratto")
    total_rows: int = Field(
        ..., ge=0, description="Righe dati nel file: imported_count + len(problems)"
    )
    problems: list[ImportProblem] = Field(
        default_factory=list, description="Una voce per ogni riga rifiutata, con il suo numero"
    )
    replayed: bool = Field(
        default=False,
        description=(
            "True se la Idempotency-Key era gia' stata usata con questo stesso file: "
            "e' un rilancio, non un nuovo caricamento."
        ),
    )

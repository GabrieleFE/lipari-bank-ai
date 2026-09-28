from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.api import accounts, advice, categorize, chat, movements
from src.config import Environment, Settings, secret_is_configured, settings
from src.db.session import engine
from src.exceptions import AppError
from src.middleware import setup_middleware
from src.types.error import ErrorDetail, ErrorResponse
from src.validation import echo_value, field_path, hint_for, terminal_field


def _dump(body: ErrorResponse) -> dict[str, object]:
    return body.model_dump(mode="json")


def details_from_request_error(exc: RequestValidationError) -> list[ErrorDetail]:
    """Il 422 di FastAPI tradotto con lo stesso vocabolario del 422 di una riga CSV.

    `exc.errors()` e' il dizionario grezzo di Pydantic: nome del tipo dell'errore,
    percorso del campo, valore ricevuto. Qui diventa un `ErrorDetail`, cosi' il
    chiamante legge `amount` / `NOT_POSITIVE` sia che l'abbia scritto a mano sia che
    l'abbia scritto in una riga del CSV.
    """
    details: list[ErrorDetail] = []
    for error in exc.errors():
        hint = hint_for(str(error["type"]), terminal_field(error["loc"]))
        details.append(
            ErrorDetail(
                field=field_path(error["loc"]),
                code=hint.code,
                message=hint.message,
                expected=hint.expected,
                received=echo_value(error.get("input")),
            )
        )
    return details


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Bootcamp Python AI-Powered v3",
    lifespan=lifespan,
)

setup_middleware(app)


@app.exception_handler(AppError)
async def app_exception_handler(req: Request, exc: AppError) -> JSONResponse:
    details = exc.details
    if not details and exc.retry_after is not None:
        details = [ErrorDetail(field="retry_after", code="RATE_LIMIT", message=exc.message)]
    body = ErrorResponse(
        timestamp=datetime.now(UTC),
        status=exc.status_code,
        error=exc.code,
        message=exc.message,
        path=req.url.path,
        details=details,
    )
    headers: dict[str, str] = {}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(status_code=exc.status_code, content=_dump(body), headers=headers)


@app.exception_handler(Exception)
async def general_exception_handler(req: Request, exc: Exception) -> JSONResponse:
    # logger.exception(exc) in G7
    body = ErrorResponse(
        timestamp=datetime.now(UTC),
        status=500,
        error="INTERNAL_ERROR",
        message="Errore inatteso",
        path=req.url.path,
    )
    return JSONResponse(status_code=500, content=_dump(body))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(req: Request, exc: RequestValidationError) -> JSONResponse:
    """Il 422 di FastAPI entra nella stessa busta di ogni altro errore.

    Non e' un caso diverso dal 500 o dal 400 di dominio: e' lo stesso problema
    ('questa richiesta non e' valida') con un'altra causa. Il chiamante vede
    `ErrorResponse` e i suoi `details`, identici a quelli di un errore di dominio.
    """
    body = ErrorResponse(
        timestamp=datetime.now(UTC),
        status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        error="VALIDATION_ERROR",
        message="Richiesta non valida",
        path=req.url.path,
        details=details_from_request_error(exc),
    )
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content=_dump(body))


class CredentialsHealth(BaseModel):
    openai_configured: bool
    anthropic_configured: bool
    missing: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["UP", "DEGRADED"]
    timestamp: datetime
    app_name: str
    version: str
    environment: Environment
    credentials: CredentialsHealth


def build_health_response(config: Settings) -> HealthResponse:
    openai_configured = secret_is_configured(config.openai_api_key)
    anthropic_configured = secret_is_configured(config.anthropic_api_key)
    missing: list[str] = []
    if not openai_configured:
        missing.append("OPENAI_API_KEY")
    if not anthropic_configured:
        missing.append("ANTHROPIC_API_KEY")
    credentials = CredentialsHealth(
        openai_configured=openai_configured,
        anthropic_configured=anthropic_configured,
        missing=missing,
    )
    return HealthResponse(
        status="UP" if not missing else "DEGRADED",
        timestamp=datetime.now(UTC),
        app_name=config.app_name,
        version="1.0.0",
        environment=config.environment,
        credentials=credentials,
    )


@app.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "Credenziali mancanti"}},
)
async def health(response: Response) -> HealthResponse:
    result = build_health_response(settings)
    if result.status == "DEGRADED":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result


app.include_router(chat.router)
app.include_router(categorize.router)
app.include_router(advice.router)
app.include_router(movements.router)
app.include_router(accounts.router)

# Test console statica (Giorno 6): servita dalla stessa origin, niente CORS.
# Il mount va DOPO i router: le route API hanno priorita', il resto atterra su web/.
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

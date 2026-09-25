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

from src.api import advice, categorize, chat
from src.config import Environment, Settings, secret_is_configured, settings
from src.db.session import engine
from src.exceptions import AppError
from src.middleware import setup_middleware


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
    content: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "status": exc.status_code,
        "error": exc.code,
        "message": exc.message,
        "path": req.url.path,
    }
    headers: dict[str, str] = {}
    if exc.retry_after is not None:
        content["retry_after"] = exc.retry_after
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(status_code=exc.status_code, content=content, headers=headers)


@app.exception_handler(Exception)
async def general_exception_handler(req: Request, exc: Exception) -> JSONResponse:
    # logger.exception(exc) in G7
    return JSONResponse(
        status_code=500,
        content={
            "timestamp": datetime.now(UTC).isoformat(),
            "status": 500,
            "error": "INTERNAL_ERROR",
            "message": "Errore inatteso",
            "path": req.url.path,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(req: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,  # default FastAPI
        content={
            "timestamp": datetime.now(UTC).isoformat(),
            "status": 422,
            "error": "VALIDATION_ERROR",
            "message": "Input non valido",
            "path": req.url.path,
            "details": [f"{e['loc'][-1]}: {e['msg']}" for e in exc.errors()],
        },
    )


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

# Test console statica (Giorno 6): servita dalla stessa origin, niente CORS.
# Il mount va DOPO i router: le route API hanno priorita', il resto atterra su web/.
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

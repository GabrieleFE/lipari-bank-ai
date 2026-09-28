import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings


def setup_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_request_id(request: Request, call_next: Callable[..., Any]) -> Response:
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        start = time.perf_counter()
        response: Response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Process-Time"] = f"{time.perf_counter() - start:.4f}"
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        # `Idempotency-Key` serve al rilancio dell'import. Senza questa voce la pagina
        # su un'altra origine verrebbe bloccata dal preflight: l'intestazione che
        # l'ufficio usa per non raddoppiare le righe non arriverebbe mai al server.
        allow_headers=["Authorization", "Content-Type", "X-Request-Id", "Idempotency-Key"],
        allow_credentials=True,
    )

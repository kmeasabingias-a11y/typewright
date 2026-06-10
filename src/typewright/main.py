"""FastAPI application: TypeWright's public HTTP surface.

Phase 1 exposes two routes: a ``/health`` liveness check and ``POST /v1/analyze``,
which parses a Python function and returns its metadata (DECISIONS.md D4, D5). LLM
contract inference, test generation, and sandbox execution arrive in later phases;
this endpoint returns only the honest subset it can produce today.

The app is built by a ``create_app()`` factory so tests can construct a fresh,
fully-configured instance, while ``app`` at module scope is what ``uvicorn`` serves
(``uvicorn typewright.main:app``).
"""

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import get_settings
from .errors import TypeWrightError
from .logging_config import configure_logging
from .models import AnalyzedFunction, AnalyzeRequest, AnalyzeResponse
from .parser import parse_function

logger = logging.getLogger("typewright")


def create_app() -> FastAPI:
    """Build and configure the TypeWright FastAPI application."""
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(title=settings.app_name, version="0.1.0")

    @app.exception_handler(TypeWrightError)
    async def handle_domain_error(
        request: Request, exc: TypeWrightError
    ) -> JSONResponse:
        """Map any caller-facing domain error to 400 Bad Request (DECISIONS.md D8).

        Anything that is not a ``TypeWrightError`` is left to FastAPI's default
        handling, which returns 500 — our signal that the bug is ours, not the
        caller's.
        """
        logger.info("analyze rejected: %s", exc)
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness probe: returns 200 as long as the process is serving."""
        return {"status": "ok"}

    @app.post("/v1/analyze", response_model=AnalyzeResponse)
    def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
        """Parse one Python function and return its metadata (Phase 1 subset)."""
        metadata = parse_function(request.code, request.function_name)
        logger.info("analyzed function %r", metadata.name)
        return AnalyzeResponse(
            analysis_id=str(uuid.uuid4()),
            function=AnalyzedFunction.from_metadata(metadata),
        )

    return app


app = create_app()

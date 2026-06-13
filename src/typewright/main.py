"""FastAPI application: TypeWright's public HTTP surface.

Phase 2 exposes two routes: a ``/health`` liveness check and ``POST /v1/analyze``,
which parses a Python function and returns its metadata plus the LLM-inferred
``contract`` (DECISIONS.md D4, D5, D21). Test generation and sandbox execution
arrive in later phases; this endpoint returns only the honest subset it can
produce today.

The app is built by a ``create_app()`` factory so tests can construct a fresh,
fully-configured instance, while ``app`` at module scope is what ``uvicorn`` serves
(``uvicorn typewright.main:app``).
"""

import logging
import uuid
from typing import Callable

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from .config import get_settings
from .errors import PipelineError, TypeWrightError
from .inference import infer_contract
from .logging_config import configure_logging
from .models import AnalyzedFunction, AnalyzeRequest, AnalyzeResponse, Contract
from .parser import parse_function

logger = logging.getLogger("typewright")


def get_infer_contract() -> Callable[..., Contract]:
    """Dependency provider for the contract-inference step.

    Returning the function (rather than calling it inline) gives tests a clean
    seam: ``app.dependency_overrides[get_infer_contract]`` swaps in a fake that
    returns a known ``Contract``, so API tests run with no live LLM key (D21).
    """
    return infer_contract


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

    @app.exception_handler(PipelineError)
    async def handle_pipeline_error(
        request: Request, exc: PipelineError
    ) -> JSONResponse:
        """Map an internal pipeline failure to 500, naming the failing stage.

        The caller's input was valid (it parsed), but a stage of our own analysis
        — e.g. LLM contract inference — could not complete (D15). §7.1 requires
        the 500 body to report the failing stage, so it's included here.
        """
        logger.error("pipeline stage %r failed: %s", exc.stage, exc.detail)
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "stage": exc.stage},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness probe: returns 200 as long as the process is serving."""
        return {"status": "ok"}

    @app.post("/v1/analyze", response_model=AnalyzeResponse)
    def analyze(
        request: AnalyzeRequest,
        infer: Callable[..., Contract] = Depends(get_infer_contract),
    ) -> AnalyzeResponse:
        """Parse one Python function and infer its semantic contract (Phase 2).

        Parsing failures are caller errors (-> 400); a failure inside contract
        inference raises ``PipelineError`` (-> 500 with the failing stage).
        """
        metadata = parse_function(request.code, request.function_name)
        contract = infer(metadata, model_tier=request.model_tier)
        logger.info("analyzed function %r", metadata.name)
        return AnalyzeResponse(
            analysis_id=str(uuid.uuid4()),
            function=AnalyzedFunction.from_metadata(metadata),
            contract=contract,
        )

    return app


app = create_app()

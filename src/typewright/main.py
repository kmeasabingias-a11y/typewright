"""FastAPI application: TypeWright's public HTTP surface.

Phase 4 exposes two routes: a ``/health`` liveness check and ``POST /v1/analyze``,
which parses a Python function, detects the property classes it satisfies, generates
a Hypothesis strategy per argument, and generates a complete pytest file asserting
those properties (DECISIONS.md D4, D5, D21, D23, D30, D36). Bug-finding, fix
suggestions, and sandbox execution arrive in later phases; this endpoint returns only
the honest subset it can produce today.

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
from .generation import generate_strategies
from .inference import infer_properties
from .logging_config import configure_logging
from .models import (
    AnalyzedFunction,
    AnalyzeRequest,
    AnalyzeResponse,
    GeneratedTestFile,
    PropertyAnalysis,
    StrategyPlan,
)
from .parser import parse_function
from .testgen import generate_test_file

logger = logging.getLogger("typewright")


def get_infer_properties() -> Callable[..., PropertyAnalysis]:
    """Dependency provider for the property-detection step.

    Returning the function (rather than calling it inline) gives tests a clean
    seam: ``app.dependency_overrides[get_infer_properties]`` swaps in a fake that
    returns a known ``PropertyAnalysis``, so API tests run with no live LLM key
    (D21).
    """
    return infer_properties


def get_generate_strategies() -> Callable[..., StrategyPlan]:
    """Dependency provider for the strategy-generation step.

    Mirrors ``get_infer_properties``: returning the function lets tests override it
    via ``app.dependency_overrides[get_generate_strategies]`` and run with no live
    key (D21, D28, D30).
    """
    return generate_strategies


def get_generate_test_file() -> Callable[..., GeneratedTestFile]:
    """Dependency provider for the test-file-generation step.

    Mirrors the other two providers: returning the function lets tests override it
    via ``app.dependency_overrides[get_generate_test_file]`` and run with no live
    key (D21, D30, D36).
    """
    return generate_test_file


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
        — e.g. LLM property detection — could not complete (D15). §7.1 requires
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
        infer: Callable[..., PropertyAnalysis] = Depends(get_infer_properties),
        gen: Callable[..., StrategyPlan] = Depends(get_generate_strategies),
        gen_tests: Callable[..., GeneratedTestFile] = Depends(get_generate_test_file),
    ) -> AnalyzeResponse:
        """Parse, detect properties, generate strategies, then generate a pytest file (Phase 4).

        Parsing failures are caller errors (-> 400); a failure inside any LLM stage
        raises ``PipelineError`` (-> 500 with the failing stage). The chain is
        all-or-nothing: a failure in any step 500s rather than returning a partial
        result, so a 200 always carries a full ``test_file`` (D30, D36).
        """
        metadata = parse_function(request.code, request.function_name)
        properties = infer(metadata, model_tier=request.model_tier)
        strategy_plan = gen(metadata, properties, model_tier=request.model_tier)
        test_file = gen_tests(
            metadata, properties, strategy_plan, model_tier=request.model_tier
        )
        logger.info("analyzed function %r", metadata.name)
        return AnalyzeResponse(
            analysis_id=str(uuid.uuid4()),
            function=AnalyzedFunction.from_metadata(metadata),
            properties=properties,
            strategy_plan=strategy_plan,
            test_file=test_file,
        )

    return app


app = create_app()
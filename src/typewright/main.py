"""FastAPI application: TypeWright's public HTTP surface.

Phase 5 exposes two routes: a ``/health`` liveness check and ``POST /v1/analyze``, which
parses a Python function, detects the property classes it satisfies, generates a Hypothesis
strategy per argument, generates a complete pytest file asserting those properties, then
RUNS that file in the Kestrel sandbox and reports the bugs it finds (DECISIONS.md D4, D5,
D21, D23, D30, D36, D37–D42). Fix suggestions arrive in a later phase; this endpoint returns
only the honest subset it can produce today.

The app is built by a ``create_app()`` factory so tests can construct a fresh,
fully-configured instance, while ``app`` at module scope is what ``uvicorn`` serves
(``uvicorn typewright.main:app``).
"""

import logging
import uuid
from typing import Callable

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .errors import PipelineError, SandboxTimeoutError, TypeWrightError
from .execution import run_tests
from .generation import generate_strategies
from .inference import infer_properties
from .kestrel import SandboxResult
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
from .results import parse_results
from .testgen import generate_test_file

logger = logging.getLogger("typewright")


def get_infer_properties() -> Callable[..., PropertyAnalysis]:
    """Dependency provider for the property-detection step (test seam, D21)."""
    return infer_properties


def get_generate_strategies() -> Callable[..., StrategyPlan]:
    """Dependency provider for the strategy-generation step (test seam, D21/D28/D30)."""
    return generate_strategies


def get_generate_test_file() -> Callable[..., GeneratedTestFile]:
    """Dependency provider for the test-file-generation step (test seam, D21/D30/D36)."""
    return generate_test_file


def get_run_tests() -> Callable[..., SandboxResult]:
    """Dependency provider for the sandbox-execution step (test seam, D41).

    The seam sits at the I/O boundary: it returns ``run_tests`` (which calls Kestrel and
    yields a raw ``SandboxResult``). The route then runs the *pure* ``parse_results`` itself,
    so API tests mock only the sandbox HTTP call and exercise the real parser end-to-end.
    """
    return run_tests


def create_app() -> FastAPI:
    """Build and configure the TypeWright FastAPI application."""
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(title=settings.app_name, version="0.1.0")

    @app.exception_handler(TypeWrightError)
    async def handle_domain_error(request: Request, exc: TypeWrightError) -> JSONResponse:
        """Map any caller-facing domain error to 400 Bad Request (D8)."""
        logger.info("analyze rejected: %s", exc)
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(PipelineError)
    async def handle_pipeline_error(request: Request, exc: PipelineError) -> JSONResponse:
        """Map an internal pipeline failure to 500, naming the failing stage (D15)."""
        logger.error("pipeline stage %r failed: %s", exc.stage, exc.detail)
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "stage": exc.stage},
        )

    @app.exception_handler(SandboxTimeoutError)
    async def handle_timeout(request: Request, exc: SandboxTimeoutError) -> JSONResponse:
        """Map an exceeded test-run budget to 504 (D42).

        The caller's input was valid and no stage failed — the generated tests simply did
        not finish within the budget, so this is neither a 400 nor a 500 (§7.1).
        """
        logger.info("analyze timed out: %s", exc)
        return JSONResponse(status_code=504, content={"detail": str(exc)})

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
        run: Callable[..., SandboxResult] = Depends(get_run_tests),
        settings: Settings = Depends(get_settings),
    ) -> AnalyzeResponse:
        """Parse → detect → generate strategies → generate tests → run in the sandbox (Phase 5).

        Parsing failures are caller errors (-> 400); a failure inside any LLM stage or the
        sandbox call raises ``PipelineError`` (-> 500 with the failing stage); a run that
        exceeds its budget raises ``SandboxTimeoutError`` (-> 504, D42). The chain is
        all-or-nothing: a 200 always carries a full result, with ``bugs_found`` possibly empty
        (D30, D36, D41).
        """
        metadata = parse_function(request.code, request.function_name)
        properties = infer(metadata, model_tier=request.model_tier)
        strategy_plan = gen(metadata, properties, model_tier=request.model_tier)
        test_file = gen_tests(
            metadata, properties, strategy_plan, model_tier=request.model_tier
        )

        budget = (
            request.max_test_runtime_seconds
            if request.max_test_runtime_seconds is not None
            else settings.kestrel_timeout_seconds
        )
        sandbox_result = run(test_file, timeout_seconds=budget)
        report = parse_results(sandbox_result, properties)
        if report.timed_out:
            raise SandboxTimeoutError(budget)

        logger.info(
            "analyzed function %r: %d bug(s) found", metadata.name, len(report.bugs)
        )
        return AnalyzeResponse(
            analysis_id=str(uuid.uuid4()),
            function=AnalyzedFunction.from_metadata(metadata),
            properties=properties,
            strategy_plan=strategy_plan,
            test_file=test_file,
            bugs_found=report.bugs,
        )

    return app


app = create_app()
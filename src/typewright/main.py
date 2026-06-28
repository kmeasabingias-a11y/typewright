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

import json
import logging
import uuid
from functools import lru_cache
from typing import Awaitable, Callable
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config import Settings, get_settings
from .errors import CostBudgetExceededError, PipelineError, SandboxTimeoutError, TypeWrightError
from .execution import run_tests
from .fixgen import build_fix_file, finalize, suggest_fix
from .generation import generate_strategies
from .inference import infer_properties
from .kestrel import SandboxResult
from .logging_config import configure_logging
from .metrics import cost_scope
from .models import (
    AnalysisMetadata,
    AnalyzedFunction,
    AnalyzeRequest,
    AnalyzeResponse,
    BugReport,
    FixSuggestion,
    FunctionMetadata,
    GeneratedTestFile,
    ProposedFix,
    PropertyAnalysis,
    PullRequestJob,
    StrategyPlan,
)
from .parser import parse_function
from .results import parse_results
from .store import RunStore, SqliteRunStore
from .testgen import generate_test_file
from .web import INDEX_HTML
from .webhook import parse_pull_request_event, verify_signature
from .worker import enqueue as worker_enqueue

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


def get_suggest_fix() -> Callable[..., ProposedFix]:
    """Dependency provider for the fix-suggestion LLM step (test seam, D44).

    Only the LLM call is injected; verification reuses ``get_run_tests`` (the same sandbox
    seam), so one mock covers both the initial run and the fix's verification re-run.
    """
    return suggest_fix


def get_enqueue() -> Callable[[PullRequestJob], Awaitable[None]]:
    """Dependency provider for enqueuing a PR analysis job onto the arq queue (D46).

    Overridden in tests with a capturing fake; in production it pushes onto Redis via arq
    (``typewright.worker.enqueue``), and a separate worker process runs the analysis.
    """
    return worker_enqueue


@lru_cache
def _default_run_store() -> SqliteRunStore:
    """Build the process-wide SQLite run store from settings (created once)."""
    return SqliteRunStore(get_settings().runs_db_path)


def get_run_store() -> RunStore:
    """Dependency provider for the run store (test seam, D50).

    Production returns a process-wide ``SqliteRunStore``; tests override this with an
    ``InMemoryRunStore`` so persistence is exercised with no disk.
    """
    return _default_run_store()


def _maybe_suggest_fix(
    request: AnalyzeRequest,
    meta: FunctionMetadata,
    properties: PropertyAnalysis,
    test_file: GeneratedTestFile,
    report: BugReport,
    budget: float,
    suggest: Callable[..., ProposedFix],
    run: Callable[..., SandboxResult],
) -> FixSuggestion | None:
    """Optionally propose a fix and verify it — best-effort, never fails the request (Phase 6).

    Runs only when the caller set ``include_fix_suggestion`` AND bugs were found. The fix is
    verified by swapping the corrected function into the SAME test file (``build_fix_file``) and
    re-running it through the same injected sandbox seam (D45). Unlike the all-or-nothing main
    chain (D30/D36/D41), every failure here degrades to a ``null`` / ``verified=false`` suggestion
    rather than sinking the already-valid ``bugs_found`` (D44): a fix-gen LLM error -> null; an
    unrunnable corrected file or a verification timeout/transport error -> ``verified=false``.
    """
    if not request.include_fix_suggestion or not report.bugs:
        return None
    try:
        proposed = suggest(meta, report, model_tier=request.model_tier)
    except (PipelineError, CostBudgetExceededError) as exc:
        logger.warning("fix suggestion skipped (%s): %s", type(exc).__name__, exc)
        return None

    fix_file = build_fix_file(test_file, meta, proposed)
    verify_report: BugReport | None = None
    if fix_file is not None:
        try:
            verify_result = run(fix_file, timeout_seconds=budget)
            if not verify_result.timed_out:
                verify_report = parse_results(verify_result, properties)
        except PipelineError as exc:
            logger.warning("fix verification inconclusive (sandbox failed): %s", exc)
    return finalize(proposed, verify_report)


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
    
    @app.exception_handler(CostBudgetExceededError)
    async def handle_cost_budget(request: Request, exc: CostBudgetExceededError) -> JSONResponse:
        """Map an exceeded LLM-cost budget to 402 Payment Required (D52)."""
        logger.info("analyze aborted on cost budget: %s", exc)
        return JSONResponse(
            status_code=402,
            content={
                "detail": str(exc),
                "spent_usd": round(exc.spent_usd, 6),
                "limit_usd": exc.limit_usd,
            },
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness probe: returns 200 as long as the process is serving."""
        return {"status": "ok"}
    
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        """Serve the Phase 8 web demo: a self-contained paste-a-function page (D49).

        One static HTML document (inline CSS + vanilla JS, no build step, no external assets)
        that POSTs to ``POST /v1/analyze`` on this same origin with ``include_fix_suggestion``
        set, and renders the detected properties, the failing inputs, and the verified fix. It
        is served as a module constant so it ships in the wheel/image with no static-asset
        packaging (D49); excluded from the OpenAPI schema since it is a page, not an API.
        """
        return INDEX_HTML
    
    @app.post("/webhook/github")
    async def github_webhook(
        request: Request,
        enqueue: Callable[[PullRequestJob], Awaitable[None]] = Depends(get_enqueue),
        settings: Settings = Depends(get_settings),
    ) -> JSONResponse:
        """Receive a GitHub webhook: verify, parse, enqueue, acknowledge fast (Phase 7, D47).

        The signature is checked against the RAW body. Only actionable ``pull_request`` events
        (opened / synchronize / reopened) enqueue a job; everything else is acknowledged and
        ignored. We never analyze inline — GitHub needs a quick 2xx, so the work is queued (202)
        and a worker handles it (Unit 5).
        """
        body = await request.body()
        secret = settings.github_webhook_secret
        if secret:
            if not verify_signature(body, request.headers.get("X-Hub-Signature-256"), secret):
                logger.warning("github webhook: invalid signature")
                return JSONResponse(status_code=403, content={"detail": "invalid signature"})
        else:
            logger.warning(
                "github_webhook_secret unset — skipping signature verification (dev only)"
            )

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"detail": "invalid JSON body"})

        job = parse_pull_request_event(request.headers.get("X-GitHub-Event", ""), payload)
        if job is None:
            return JSONResponse(status_code=200, content={"status": "ignored"})

        await enqueue(job)
        logger.info(
            "queued PR analysis: %s #%d @ %s",
            job.repo_full_name,
            job.pr_number,
            job.head_sha[:7],
        )
        return JSONResponse(status_code=202, content={"status": "queued", "pr": job.pr_number})

    @app.post("/v1/analyze", response_model=AnalyzeResponse)
    def analyze(
        request: AnalyzeRequest,
        infer: Callable[..., PropertyAnalysis] = Depends(get_infer_properties),
        gen: Callable[..., StrategyPlan] = Depends(get_generate_strategies),
        gen_tests: Callable[..., GeneratedTestFile] = Depends(get_generate_test_file),
        run: Callable[..., SandboxResult] = Depends(get_run_tests),
        suggest: Callable[..., ProposedFix] = Depends(get_suggest_fix),
        store: RunStore = Depends(get_run_store),
        settings: Settings = Depends(get_settings),
    ) -> AnalyzeResponse:
        """Parse → detect → strategies → tests → run in the sandbox → optional fix (Phase 6).

        Parsing failures are caller errors (-> 400); a failure inside any LLM stage or the
        sandbox call raises ``PipelineError`` (-> 500 with the failing stage); a run that
        exceeds its budget raises ``SandboxTimeoutError`` (-> 504, D42). The core chain is
        all-or-nothing: a 200 always carries a full result, with ``bugs_found`` possibly empty
        (D30, D36, D41). The Phase 6 fix step is opt-in and best-effort (D44). The pipeline runs
        inside a ``cost_scope`` so every LLM call bills one meter, filling ``metadata`` (Phase 9, D51).
        """
        cost_budget = (
            settings.max_cost_usd
            if request.max_cost_usd is None
            else min(request.max_cost_usd, settings.max_cost_usd)
        )
        start = time.perf_counter()
        with cost_scope(cost_budget) as meter:
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

            fix_suggestion = _maybe_suggest_fix(
                request, metadata, properties, test_file, report, budget, suggest, run
            )

        analysis_metadata = AnalysisMetadata(
            analysis_duration_ms=int((time.perf_counter() - start) * 1000),
            llm_cost_usd=round(meter.total_usd, 6),
            tests_generated=len(test_file.test_names),
            tests_run=report.tests_passed + report.tests_failed,
        )

        logger.info(
            "analyzed function %r: %d bug(s) found%s — $%.4f, %dms",
            metadata.name,
            len(report.bugs),
            "" if fix_suggestion is None else f", fix verified={fix_suggestion.verified}",
            analysis_metadata.llm_cost_usd,
            analysis_metadata.analysis_duration_ms,
        )
        response = AnalyzeResponse(
            analysis_id=str(uuid.uuid4()),
            function=AnalyzedFunction.from_metadata(metadata),
            properties=properties,
            strategy_plan=strategy_plan,
            test_file=test_file,
            bugs_found=report.bugs,
            fix_suggestion=fix_suggestion,
            metadata=analysis_metadata,
        )
        try:
            store.save(response)
        except Exception:  # best-effort: a store failure must not sink a valid analysis (D44/D50)
            logger.warning("failed to persist run %s", response.analysis_id, exc_info=True)
        return response
    

    @app.get("/v1/runs/{analysis_id}", response_model=AnalyzeResponse)
    def get_run(
        analysis_id: str,
        store: RunStore = Depends(get_run_store),
    ) -> AnalyzeResponse:
        """Fetch a previously-run analysis by id — the shareable-link read path (Phase 8, D50).

        Returns the stored ``AnalyzeResponse`` as-is, or **404** when the id is unknown (expired or
        never existed). A miss is a plain ``HTTPException`` — not a caller-input error (400) and not a
        pipeline failure (500), so it needs no domain-error type.
        """
        run = store.load(analysis_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    return app


app = create_app()
# 06 — `src/typewright/main.py`

## What this file is for

This file is TypeWright's **front desk**.

Everything we've built so far — the settings panel, the logger, the data shapes,
the error slips, the parser, and the two AI steps (property detection and strategy
generation) — lives *inside* the building. This file is the receptionist at the door:
it listens for visitors arriving over the web, takes their request, walks it through
the right rooms (parser → detector → strategy generator), and brings back the answer —
or, if something's wrong, hands them the right "sorry, here's why" slip.

The front desk offers exactly two things:

1. **A health check** (`/health`) — a doorbell anyone can press to confirm the
   service is alive.
2. **The analysis endpoint** (`POST /v1/analyze`) — you hand it a Python function
   as text; it parses the function, detects the property classes it satisfies, **and**
   generates a Hypothesis strategy for each argument, then hands all of it back as JSON.

> **If you read the old version of this doc:** this route grew one phase at a time. Phase 1
> only parsed and returned `{analysis_id, function}`. Phase 2 wired in the first AI step
> (detection), adding `properties` and a second error handler for when *our own* step fails
> (a 500). Phase 3 wired in the second AI step (strategy generation): one more injected
> dependency, one more chained call, and a new `strategy_plan` field in the response.

---

## A mental model: web requests, FastAPI, and status codes

A few ideas make this file read easily.

**1. A web service is a request/response loop.** A client (a browser, `curl`, our
future web demo) sends an HTTP **request** to an address; the service sends back a
**response**. The request says *what* it wants (here: "analyze this code"); the
response carries the result plus a **status code** — a 3-digit number summarizing
how it went.

**2. Status codes, in three buckets we care about:**
- **2xx = success.** `200 OK` — here's your answer.
- **4xx = the caller's mistake.** `400 Bad Request` — *you* sent something we can't
  use (bad code, missing function). `422` — the request didn't even match the
  expected shape.
- **5xx = our mistake.** `500 Internal Server Error` — *we* hit a problem (e.g. the AI
  step couldn't complete).

That 4xx-vs-5xx line is the same "caller's fault vs our fault" split the errors file
(04) set up — now it becomes real HTTP numbers, on **both** sides.

**3. FastAPI does the plumbing.** **FastAPI** is the web framework we use. We don't
write code to read raw network bytes; we just declare "when a POST arrives at
`/v1/analyze`, run this function," and FastAPI handles parsing the incoming JSON,
checking it against our `AnalyzeRequest` shape, calling our function, and turning
the returned object back into JSON.

**4. A "factory" function.** Instead of building the app at the top level directly,
we wrap the setup in a function, `create_app()`. Calling it builds and returns a
fully wired app. This is handy because tests can call it to get a fresh, clean app
whenever they want — and we still expose one ready-made `app` at the bottom for the
real server to run.

**5. Dependency injection: the route asks for what it needs.** FastAPI lets a route
*declare* the helpers it wants as parameters with `= Depends(...)`, and FastAPI supplies
them. We use this for *both* AI steps — detection and generation. Why bother, instead of
calling `infer_properties` / `generate_strategies` directly? Because it gives tests a clean
**seam**: a test can say "for this app, swap in fake AI steps" and the route never knows the
difference — so the whole HTTP test suite runs with no live AI key (decision **D21**). Think
of it as the route ordering its helpers off a menu, rather than walking into the kitchen to
cook them — which means the test can quietly change the menu.

---

## The whole file

```python
"""FastAPI application: TypeWright's public HTTP surface.

Phase 3 exposes two routes: a ``/health`` liveness check and ``POST /v1/analyze``,
which parses a Python function, detects the property classes it satisfies, and
generates a Hypothesis strategy per argument (DECISIONS.md D4, D5, D21, D23, D30).
Bug-finding, fix suggestions, and sandbox execution arrive in later phases; this
endpoint returns only the honest subset it can produce today.

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
    PropertyAnalysis,
    StrategyPlan,
)
from .parser import parse_function

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
    ) -> AnalyzeResponse:
        """Parse a function, detect its property classes, and generate strategies (Phase 3).

        Parsing failures are caller errors (-> 400); a failure inside either LLM
        stage raises ``PipelineError`` (-> 500 with the failing stage). The chain is
        all-or-nothing: a generation failure after a successful detection still 500s
        (D30).
        """
        metadata = parse_function(request.code, request.function_name)
        properties = infer(metadata, model_tier=request.model_tier)
        strategy_plan = gen(metadata, properties, model_tier=request.model_tier)
        logger.info("analyzed function %r", metadata.name)
        return AnalyzeResponse(
            analysis_id=str(uuid.uuid4()),
            function=AnalyzedFunction.from_metadata(metadata),
            properties=properties,
            strategy_plan=strategy_plan,
        )

    return app


app = create_app()
```

---

## Step-by-step

### The imports and the logger

```python
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
    PropertyAnalysis,
    StrategyPlan,
)
from .parser import parse_function

logger = logging.getLogger("typewright")
```

The top half pulls in tools: `logging`, `uuid` (to mint a unique id per analysis), `Callable`
(a type hint for "a function"), and the FastAPI pieces — including **`Depends`** (the
dependency-injection helper). The bottom half pulls in *our own* building blocks: settings,
**both** error types, the logging setup, the data shapes (including `PropertyAnalysis` and
`StrategyPlan`), the parser, and the two AI steps — `infer_properties` (unit 09) and, new in
Phase 3, `generate_strategies` (unit 11).

`logger = logging.getLogger("typewright")` grabs a named logger so our log lines are clearly
tagged as coming from TypeWright (you saw that tag in the logging walkthrough, 02).

### `get_infer_properties` / `get_generate_strategies` — the swappable seams

```python
def get_infer_properties() -> Callable[..., PropertyAnalysis]:
    return infer_properties


def get_generate_strategies() -> Callable[..., StrategyPlan]:
    return generate_strategies
```

These two tiny functions just *return* the real AI steps. They look pointless until you see why
they exist: they're the **dependencies** the route will ask for. In normal running, FastAPI
calls them and the route gets the real detector and generator. In a test, we tell the app "when
the route asks for `get_infer_properties` (or `get_generate_strategies`), give it *this fake*
instead" — one line each (`app.dependency_overrides[...] = ...`) and the whole HTTP suite runs
with no live AI key, no network, no cost (decision **D21**). Returning the function rather than
calling it is what makes the swap clean. Phase 3 added the second provider so the new generation
step gets the same key-less test seam.

### `create_app` — assembling the front desk

```python
def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(title=settings.app_name, version="0.1.0")
```

The first two lines do start-up housekeeping: read the settings once (the cached singleton
from config), then turn on logging using them. Then we create the FastAPI application itself,
giving it a title and version (these show up in the auto-generated API docs FastAPI provides
for free).

Everything after this *attaches things to* `app`: two error handlers and two routes.

### Error handler #1 — the 400 rule (caller's fault)

```python
@app.exception_handler(TypeWrightError)
async def handle_domain_error(request, exc):
    logger.info("analyze rejected: %s", exc)
    return JSONResponse(status_code=400, content={"detail": str(exc)})
```

`@app.exception_handler(TypeWrightError)` tells FastAPI: "if any request handler raises a
`TypeWrightError`, don't crash — call *this* instead." Because every one of our caller-facing
errors (from unit 04) inherits from `TypeWrightError`, this single handler catches *all four*
of them. It logs the rejection at the calm `info` level — a bad input is normal, not alarming
— and returns a `400` with the error's message as `{"detail": "..."}`.

### Error handler #2 — the 500 rule (our fault), new in Phase 2

```python
@app.exception_handler(PipelineError)
async def handle_pipeline_error(request, exc):
    logger.error("pipeline stage %r failed: %s", exc.stage, exc.detail)
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "stage": exc.stage},
    )
```

This is the new half of the story. A `PipelineError` (unit 04) means the caller's input was
*fine* — it parsed — but one of **our own** steps, the AI property detection, couldn't
complete. That's our fault, so it must be a **500**, and this handler makes it one. Two things
to notice:

- It logs at **`error`** level (not `info`): a pipeline failure is genuinely worth alarming
  about, unlike a routine bad input.
- The response body includes **`"stage": exc.stage`** — the name of the step that broke
  (`"property_detection"`). The project brief says a 500 should report which stage failed
  (§7.1), and decision **D15** carries that name on the exception so this handler can surface
  it without parsing any message.

Together the two handlers make the whole 400/500 split explicit and symmetric: our error
*family* → 400, our *pipeline* error → 500, and anything else still falls through to FastAPI's
default 500.

### `/health` — the doorbell

```python
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

A tiny endpoint that just answers `{"status": "ok"}`. Monitoring tools (and container
orchestrators) ping this to ask "are you alive?" If the process is running well enough to
answer, it's healthy.

### `/v1/analyze` — the actual job (now FIVE steps; the listing below shows the Phase 3 three-step form — see Change history for the Phase 4 testgen step and the Phase 5 run-in-sandbox step)

```python
@app.post("/v1/analyze", response_model=AnalyzeResponse)
def analyze(
    request: AnalyzeRequest,
    infer: Callable[..., PropertyAnalysis] = Depends(get_infer_properties),
    gen: Callable[..., StrategyPlan] = Depends(get_generate_strategies),
) -> AnalyzeResponse:
    metadata = parse_function(request.code, request.function_name)
    properties = infer(metadata, model_tier=request.model_tier)
    strategy_plan = gen(metadata, properties, model_tier=request.model_tier)
    logger.info("analyzed function %r", metadata.name)
    return AnalyzeResponse(
        analysis_id=str(uuid.uuid4()),
        function=AnalyzedFunction.from_metadata(metadata),
        properties=properties,
        strategy_plan=strategy_plan,
    )
```

This is the front desk's main service. It leans on work we already did, and now runs the full
**three-step chain**:

- **`request: AnalyzeRequest`** — FastAPI reads the incoming JSON body, checks it has a `code`
  string (plus optional `function_name` and `model_tier`), and hands us a ready, validated
  object. If the body doesn't fit the shape, FastAPI rejects it with `422` before our code
  runs.
- **`infer` and `gen` = `Depends(...)`** — FastAPI supplies *both* AI steps. In production
  they're the real `infer_properties` and `generate_strategies`; in tests they're fakes. The
  route doesn't care which.
- **Step 1 — `parse_function(...)`** — pass the caller's code and optional name to the parser
  (unit 05). If the code is bad or the function is missing/ambiguous, the parser *raises* one
  of our errors → handler #1 turns it into a 400. We write no error handling here; we just let
  it raise.
- **Step 2 — `infer(metadata, model_tier=…)`** — hand the parsed function to the detector
  (unit 09); get back the `PropertyAnalysis`.
- **Step 3 — `gen(metadata, properties, model_tier=…)`** — hand *both* the function and the
  detected properties to the strategy generator (unit 11); get back the `StrategyPlan`. The
  same `model_tier` drives both AI calls. If *either* AI step fails it raises a `PipelineError`
  → handler #2 turns it into a 500 naming the stage. The chain is **all-or-nothing** (D30): a
  generation failure even after a *successful* detection still 500s — we don't return a
  half-analysis dressed up as complete. Again, no `try/except` here — the handler owns that.
- **`AnalyzeResponse(...)`** — assemble the honest Phase 3 response: a fresh `analysis_id`, the
  lean `function` view (unit 03), the `properties`, and the `strategy_plan`.

- **`response_model=AnalyzeResponse`** — tells FastAPI the exact shape to send back, which
  also keeps the auto-generated docs honest.

Note the order matters: generation *depends on* detection's output (it's handed `properties`),
so Step 3 runs strictly after Step 2. This is the "deterministic chain" the brief describes —
each step's output feeds the next.

### `app = create_app()` — the one the server runs

```python
app = create_app()
```

Finally, we call the factory once to build a ready-to-serve application and name it `app`. When
you run `uvicorn typewright.main:app`, that's the object it picks up.

---

## Trying it out

Start the server (you'll need `ANTHROPIC_API_KEY` set in the environment, since the analyze
route now makes a real AI call):

```sh
export ANTHROPIC_API_KEY=sk-...     # the detection step needs a key
uv run uvicorn typewright.main:app --reload
```

Then, from another terminal:

```sh
curl -sS http://127.0.0.1:8000/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{"code": "def slugify(text: str) -> str:\n    \"\"\"Lowercase, trim, hyphenate.\"\"\"\n    return text"}'
```

You get back JSON with the function's details (`name`, `signature`, `args`, …), an
`analysis_id`, a `properties` block (the property classes the model recognised — for `slugify`,
expect idempotence and a type-postcondition), **and** a `strategy_plan` (a Hypothesis strategy
per argument — for `slugify`, `text: st.text()`). If you have no key set, that same request
returns a `500` whose body names the failing stage (`property_detection`, since detection runs
first) — exactly the our-fault path working as designed.

---

## What could go wrong

### 1. Mislabeling whose fault it is
The two handlers are what keep the 400/500 line honest. A caller error (`TypeWrightError`) must
be a 400; *our* AI step failing (`PipelineError`) must be a 500. If we'd instead wrapped
everything in a broad `try/except: return 400`, a genuine failure of our own would masquerade
as "your input was bad." Splitting the two by exception type means the status code always tells
the truth about whose fault it was.

### 2. Returning fields we can't honestly fill
It would be easy to return `"bugs_found": []` now, to "look like" the full spec. But that would
falsely imply we looked for bugs and found none. We return `analysis_id`, `function`,
`properties`, and `strategy_plan` — the honest subset Phase 3 can actually produce — and add
each further field in the phase that makes it real (DECISIONS.md D5).

### 3. Tests that secretly need a live AI key
If the route called `infer_properties` / `generate_strategies` directly, every API test would
need a real key and a network. The two `Depends(...)` seams let tests override both AI steps
with fakes (D21), so the whole HTTP suite runs offline, fast, and deterministically.

### 4. Building the app at import time with no way to get a clean one
Exposing only a single, pre-built `app` would make tests share one global instance. The
`create_app()` factory means a test can build a fresh, isolated app whenever it needs to —
while the real server still gets its ready-made `app` at the bottom.

### 5. Forgetting that schema errors are a separate path
A request missing `code` entirely never reaches our code — FastAPI rejects it with `422`
during validation. That's correct and intentional: `400` is "your *code* didn't parse," `422`
is "your *request* didn't even have the right fields," and `500` is "*we* broke." Three
different situations, three different signals.

### 6. Two LLM calls per request — and all-or-nothing
Every `/v1/analyze` now makes *two* sequential AI calls (detect, then generate), so latency and
cost roughly double, and a failure in *either* fails the whole request (D30). We chose that on
purpose: "analyze" means the full pipeline, and a partial `200` (properties but no strategies)
would be a half-truth. Trimming the cost (caching, an opt-out flag, parallelism) is a deliberate
later concern (Phase 9), not something to fake around now.

---

## Summary

`main.py` is the front desk that turns TypeWright from a pile of modules into a running web
service. It builds a FastAPI app via a `create_app()` factory and wires in **two** error
handlers — our `TypeWrightError` family → `400` (caller's fault) and `PipelineError` → `500`
with the failing stage (our fault, D15) — plus two routes: a `/health` doorbell and the
`POST /v1/analyze` endpoint. By Phase 3 the analyze route runs the full three-step chain: it
parses the function (unit 05), detects its property classes (unit 09), and generates a
Hypothesis strategy per argument (unit 11) — reaching both AI steps through `Depends(...)` seams
so tests can swap in fakes and run with no live key (D21). The same `model_tier` drives both
calls, and the chain is all-or-nothing (D30). The honest response now carries `analysis_id`,
`function`, `properties`, and `strategy_plan`. With this file, you can `curl` a function and get
its details, its detected properties, and the strategies for its inputs back in one shot.

---

## Change history

- **2026-06-10** — Created in Phase 1, Unit 6. `create_app()` factory with a `TypeWrightError`
  → 400 handler (D8), a `/health` check, and `POST /v1/analyze` returning the honest
  `{analysis_id, function}` subset (D4, D5). Module-level `app` for uvicorn. Verified
  end-to-end with FastAPI's TestClient.
- **2026-06-13** — **Wired in Phase 2 property detection (D21, D23).** Added the
  `get_infer_properties` dependency provider and injected it into `/v1/analyze` via
  `Depends(...)`, so the route now parses *and* detects, returning
  `{analysis_id, function, properties}`. Added a second exception handler mapping
  `PipelineError` → 500 with the failing `stage` in the body (D15). The request's `model_tier`
  is passed through to the detector. Suite green at 30 passed.
- **2026-06-14** — **Wired in Phase 3 strategy generation (D30).** Added the
  `get_generate_strategies` dependency and a third step to `/v1/analyze`, so the route now
  parses → detects → generates and returns `{analysis_id, function, properties, strategy_plan}`.
  The same `model_tier` drives both AI calls; failures stay all-or-nothing (any stage → 500
  naming it). Suite green at 37 passed.
- **2026-06-15** — **Wired in Phase 4 test generation (D36).** Added a third dependency,
  `get_generate_test_file`, and a fourth step to `/v1/analyze`, so the route now parses → detects
  → generates strategies → **generates the pytest file**, returning `{analysis_id, function,
  properties, strategy_plan, test_file}`. The same `model_tier` drives all three AI calls; failure
  stays all-or-nothing (graceful degradation was considered and rejected — D36), so a 200 always
  carries a full `test_file`. The route/listing in the body above still shows the Phase 3
  three-step form for teaching; the live route has the fourth `gen_tests(metadata, properties,
  strategy_plan, …)` step and the `test_file=test_file` field. `conftest.py` now mocks all three
  AI steps (unit 07). Suite green at 48 passed.
- **2026-06-19** — **Wired in Phase 5 sandbox execution (D41/D42).** Added a fourth dependency,
  `get_run_tests`, and a fifth step to `/v1/analyze`: after generating the pytest file, the route
  now **runs it in the Kestrel sandbox** and returns `bugs_found` (the failing inputs) — so the
  response is `{analysis_id, function, properties, strategy_plan, test_file, bugs_found}`. The seam
  sits at the I/O boundary (D41): the dependency injects `run_tests` (the Kestrel call, returning a
  raw `SandboxResult`), and the route then runs the *pure* `parse_results` (unit 15) itself — so the
  HTTP suite mocks only the sandbox call and exercises the real parser end-to-end. The run budget is
  `request.max_test_runtime_seconds` or the `kestrel_timeout_seconds` config default. A **third error
  handler** was added: a `SandboxTimeoutError` (raised when the run reports `timed_out`) maps to
  **504** (D42), joining the 400 (caller) and 500 (pipeline) handlers — a timed-out run is *not* a
  clean 200 with no bugs. `conftest.py` now mocks all four steps (unit 07). Suite green at 72 passed.
- **2026-06-25** — **Wired in Phase 6 fix suggestions (D44/D45).** Added a fifth dependency,
  `get_suggest_fix`, and an **opt-in, best-effort** step after the sandbox run, orchestrated by a small
  module-level `_maybe_suggest_fix` helper. It runs **only** when `request.include_fix_suggestion` is set
  **and** bugs were found: it calls `suggest_fix` (unit 17) for a corrected function, swaps it into the
  same test file with `build_fix_file`, **re-runs that through the existing `get_run_tests` seam** (so one
  mock covers both the initial and the verification run, D45), runs the pure `parse_results`, and
  `finalize`s the verdict — adding `fix_suggestion` (`FixSuggestion | None`) to the response. This step is
  the deliberate **exception to all-or-nothing** (D44): a fix-gen `PipelineError` → `fix_suggestion: null`;
  an unrunnable fix or a verification timeout/transport error → `verified=false` — none become a 500/504,
  because the analysis (`bugs_found`) is already valid and must not be discarded. No new error handler is
  needed (the fix step swallows its own failures). `conftest.py` now mocks all five steps (unit 07). Suite
  green at 92 passed.
- **2026-06-25** — **Added the Phase 7 webhook (D47) + wired the real enqueue (D46).** New
  `POST /webhook/github` route: read the **raw** body, verify the HMAC signature (skipped with a warning
  when no secret — dev only), parse the `pull_request` event (`webhook.py`, unit 18), and enqueue via the
  `get_enqueue` seam — replying **202** (queued) / **200** (ignored) / **403** (bad signature) / **400**
  (bad JSON). `get_enqueue` started as a log-only stub in Unit 1, then in Unit 5 was repointed to the real
  arq `worker.enqueue` (the web process pushes onto Redis; a separate worker process runs the analysis,
  unit 23). Verified live on a real PR (`queued PR analysis … #1` → `202`). Suite green at 134 passed.
- **2026-06-28** — **Added the Phase 8 web-demo route (D49, unit 24).** New `GET /` returns the
  self-contained demo page (`response_class=HTMLResponse`, `include_in_schema=False`) — a thin handler that
  just returns the `INDEX_HTML` constant imported from the new `web.py`. Also imported `HTMLResponse`
  alongside `JSONResponse`. No pipeline change: the page is served from the same app and POSTs to the
  existing `POST /v1/analyze` on the same origin (no CORS). Suite green at 136 passed.
- **2026-06-28** — **Added shareable-link persistence + read route (D50, unit 25).** New `get_run_store`
  dependency (an `lru_cache`d process-wide `SqliteRunStore` from `runs_db_path`; tests override with
  `InMemoryRunStore`). `POST /v1/analyze` now builds the response into a variable and **best-effort**
  `store.save(response)` before returning — a `save` failure is logged and swallowed so a storage hiccup
  never sinks the valid analysis (the D44 rule). New `GET /v1/runs/{analysis_id}` returns the stored
  `AnalyzeResponse`, or **404** via `HTTPException` on an unknown id (imported `HTTPException`; a miss is
  neither a 400 caller error nor a 500 pipeline failure, so no domain-error type). Suite green at 142 passed.
- **2026-06-28** — **Phase 9 (Unit 1, D51): the route now reports cost + timing.** `analyze` wraps the
  pipeline in `metrics.cost_scope()` (so every LLM call underneath bills one meter) and times it with
  `time.perf_counter()`, then builds an `AnalysisMetadata` (`analysis_duration_ms`, `llm_cost_usd` from the
  meter, `tests_generated` = `len(test_file.test_names)`, `tests_run` = `tests_passed + tests_failed`,
  `hypothesis_examples_tried=None`) into the new `AnalyzeResponse.metadata`. No status-code change; errors
  (500/504) still carry no metadata. Suite green at 148 passed. See `26_metrics.md`.
- **2026-06-28** — **Phase 9 (Unit 2, D52): per-analysis cost budget → 402.** The route now opens
  `cost_scope(min(request.max_cost_usd or ∞, settings.max_cost_usd))`, so the meter aborts the pipeline once
  spend crosses the ceiling. New handler maps `CostBudgetExceededError` → **402** with `spent_usd`/`limit_usd`.
  `_maybe_suggest_fix` also catches it → degrades to `fix_suggestion: null` (so a 402 only escapes the core
  pipeline, never sinks already-valid `bugs_found`, D44). Suite green at 152 passed.
- **2026-06-28** — **Phase 9 (Unit 3, D53): rate limiting → 429.** `create_app` builds a `RateLimiter` from
  `rate_limit_backend` onto `app.state`; a `get_rate_limiter(request)` dependency reads it (per app instance,
  overridable in tests). `/v1/analyze` checks `analyze:{_client_ip(...)}` (peer IP, or the first
  `X-Forwarded-For` when `trust_forwarded_for`), the webhook checks `webhook:{installation_id}` after parsing
  — both raise `RateLimitedError` when over, mapped by a new handler to **429** + a `Retry-After` header.
  Cheap GETs stay unlimited. Suite green at 157 passed.
- **2026-06-28** — **Phase 9 (Unit 4, D54): per-analysis tracing.** `analyze` hoists `analysis_id` to the
  top (so it's the trace id too) and runs the pipeline inside `trace_scope(analysis_id, model_tier=…)`
  alongside `cost_scope`, wrapping each stage in `with span("parse"|"detection"|"strategy"|"testgen"|
  "sandbox"|"fix"):`. It `trace.set(...)` the outcome (bugs, cost, llm_calls, test counts, fix_verified)
  before the scope closes, which emits one structured summary log. `AnalysisMetadata.analysis_duration_ms`
  now comes from `trace.duration_ms` (the old standalone `start = time.perf_counter()` + the human "analyzed
  function …" info log are removed — the trace summary supersedes them; `import time` dropped). Suite green
  at 163 passed.
- **2026-06-28** — Phase 9 (Unit 5, D55): added a `SandboxUnavailableError` handler → **503** + `Retry-After`
  (when set). No route-body change beyond that — `kestrel.py` raises the new error, it propagates through the
  `sandbox` span to the handler, and `_maybe_suggest_fix` catches it (alongside `PipelineError` /
  `CostBudgetExceededError`) on the verify run so a sandbox outage there drops the fix rather than 503-ing the
  request. The 100k `code` cap is a pydantic field constraint (422), so it needs no handler. Suite green at 167 passed.
- **2026-06-30** — Phase 10 (D58): added a `MonthlyBudgetExceededError` handler → **503** + `Retry-After`
  (`spent_usd`/`limit_usd` in the body) for the global monthly cap. No route-body change — `llm.complete` raises
  it, and it propagates through the pipeline span to the handler. `_maybe_suggest_fix` also catches it (alongside
  `PipelineError` / `CostBudgetExceededError`) so a monthly-cap hit during the *fix* step drops the fix rather
  than 503-ing the already-valid analysis (D44). The 503 stays distinct from the per-analysis 402 (D52). Suite
  green at 175 passed.
- **2026-06-30** — Phase 10 (D60): added a 6th injected seam `get_verify_bug` + a `_maybe_verify_bugs` helper +
  a `verify` span in the route, after the sandbox step. It loops the reported bugs, calls `verify.verify_bug`
  for each, and stores the verdict on `bug.verification` in place — honouring `bug_verification_enabled` /
  `request.verify_findings`, and catching failures (best-effort, mirrors `_maybe_suggest_fix`: a per-bug
  `PipelineError` skips just that bug; a cost/monthly-budget error stops the rest). The trace summary gained a
  `confirmed_bugs` count. Suite green at 184 passed.
- **2026-06-30** — Phase 10 (D61): after testgen the route computes `unavailable_imports(metadata.imported_modules)`
  (non-stdlib, non-allowlist packages); when non-empty it **skips the sandbox run** (it would only
  `ModuleNotFoundError`) and returns the generated tests + the honest list, setting the new
  `AnalyzeResponse.unavailable_imports`. Otherwise the sandbox runs as before. Suite green at 190 passed.

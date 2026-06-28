# TypeWright — Project Brief (living)

> **Maintained source of truth** for the project's goal, architecture, and phase plan.
> The original founding spec is `TypeWright_Project_Brief.pdf` (kept for the record).
> Where this file and the PDF differ, **this file wins**, and `DECISIONS.md` records
> why. Keep this file current as the project evolves.
>
> **Status:** Phases 1–6 complete. `/v1/analyze` runs the full parse → detect → generate strategies →
> generate test file → run-in-sandbox chain, returning `bugs_found`, and — when the caller sets
> `include_fix_suggestion` and bugs are found — an optional `fix_suggestion`: a corrected function
> **verified** by re-running the same tests in Kestrel (D44/D45). Verified live: the Phase-6 fix smoke
> produced a verified fix for **4/4** detected golden-set bugs (100%, > the ~60% exit bar) with no false
> positive on the correct control. **Phases 1–7 complete.** The GitHub App (webhook → arq/Redis queue →
worker → diff → per-function analysis → one PR comment; D46–D48) was verified live: a real PR adding a buggy
`absolute` got a bot comment in ~24s with both property violations *and* a **verified** fix. **Phase 8 (web
demo) is in progress** — Unit 1 (the paste-a-function UI) is a single self-contained page served at `GET /`
by the API itself (inline CSS + vanilla JS, no build step, D49), POSTing to `/v1/analyze` with
`include_fix_suggestion` on the same origin; it meets the exit criterion. Shareable links + storage and
per-IP rate-limiting are deferred (Phase 9 owns limits).

## 1. The goal

Every Python codebase has the same invisible problem: engineers test the cases they
thought of and miss the ones they didn't. 80% line coverage feels safe until an empty
string, a Unicode character, or a negative integer crashes a function — or worse,
silently returns a wrong answer.

TypeWright installs as a GitHub App (and offers a web demo). On every pull request, for
each changed Python function it:

1. Parses the function to extract its structure (AST).
2. **Detects which well-known property classes the function should satisfy** (see §3).
3. Generates Hypothesis property-based test strategies for the function's inputs.
4. Writes a complete pytest file that asserts those properties.
5. Executes the tests in Kestrel (an isolated Docker sandbox), capturing counter-examples.
6. Comments on the PR with the bugs found: exact failing inputs, errors, suggested fixes.

It is a deliberate productization (GitHub App + web demo) of the LLM-driven property-based
testing technique in *Agentic Property-Based Testing* (arXiv:2510.09907), not novel
research.

## 2. Architecture

Two independent services that compose, plus a client SDK:

| Service | Responsibility | Does NOT handle |
|---|---|---|
| **TypeWright** | AI pipeline: parse code, detect properties, generate tests, format results. Entry points: GitHub webhook + web UI. | Code execution, container management, isolation. |
| **Kestrel** | Code execution: run Python in an isolated container with resource limits, return structured results over REST. | AI/LLM calls, GitHub, user-facing UI. |
| **kestrel-client** | Python SDK for Kestrel: auth, retries, result parsing. | Anything AI-related or deployment. |

The boundary keeps AI concerns and sandbox-security concerns cleanly separated; Kestrel is
independently valuable and scales on a different axis (container startup) than TypeWright
(LLM latency). See `running-test-workloads.md` for how TypeWright drives Kestrel for test
workloads.

## 3. The pipeline (a deterministic chain, not an agentic loop)

Each step is one specific LLM call with structured output — not a free-form agent.

- **Step 1 — AST parsing (no LLM).** stdlib `ast` extracts name, signature, type hints,
  docstring, body. Deterministic.
- **Step 2 — Property-class detection (LLM call #1).** The model RECOGNIZES which
  well-known property classes the function fits, reasoning from name/signature/type
  hints/docstring — it does **not** synthesize a bespoke spec from the body (that would be
  a circular oracle that only catches crashes; see D23). For each detected property it
  returns: the class, a concrete **testable relation** (e.g. `parse(format(x)) == x`), an
  optional companion function (for round-trip), a rationale, and a **confidence** (low when
  guessing, never fabricated). The AST-declared input/return types are carried alongside
  for later phases. Structured via Instructor + LiteLLM at low temperature.

  **Property classes** (a function may fit several):
  - **round_trip** — an inverse exists; one-then-the-other returns the original
    (parse/format, encode/decode, serialize/deserialize, compress/decompress).
  - **idempotence** — doing it twice equals doing it once (normalize, sanitize, sort, dedup).
  - **invariant_preservation** — a structural fact of the output holds vs the input
    (a sort keeps length and the same multiset of elements).
  - **metamorphic** — a relation between changed inputs and outputs without knowing the
    exact output (case/whitespace insensitivity, monotonicity, scaling, commutativity).
  - **type_postcondition** — the output matches the declared return type / shape.
  - **value_postcondition** — a constraint on the output *value* that follows from the
    function's intent (a tax is `>= 0`, a discounted price is within `[0, original]`, a
    probability is in `[0, 1]`). The **tightest leash**: the constraint must come from the
    name/signature/docstring — what the function is *supposed* to return — never from what
    the body computes, or it is a circular oracle (D23, D26). The most powerful class and the
    most fabrication-prone; low confidence when guessing. Preferred over `totality`.
  - **totality** — should not raise on inputs in its declared domain. The **weakest**
    class: crash-only, cannot catch wrong answers. Emitted only when nothing stronger fits.
- **Step 3 — Strategy generation (LLM call #2).** Consumes the detected properties +
  input types → Hypothesis strategies as Python code, validated with `ast.parse()` before
  acceptance; retried on parse failure.
- **Step 4 — Test generation (LLM call #3).** Consumes strategies + the testable relations
  → a complete, self-contained pytest file whose `@given` tests ASSERT each relation
  (round-trip equality, idempotence, invariants, metamorphic relations, type postconditions,
  no-crash). **Hybrid assembly (D32/D33):** the LLM writes only the test functions; TypeWright
  deterministically prepends the import header + the verbatim function under test, so the file
  runs under pytest standalone. **Validated with `ast.parse()` in TypeWright (D34)** — a static
  gate only; the **dry-run import / execution happens in the Kestrel sandbox (Step 5)**, never
  in the API process, since running generated code in-process would breach the isolation boundary
  (§2). A property whose round-trip companion is absent is recorded as skipped, not forced into an
  unrunnable test (§8 risk 3).
- **Step 5 — Execution (Kestrel, no LLM).** Combined file (function + tests) runs in the
  sandbox; returns outcomes, stdout/stderr, and Hypothesis counter-examples.
- **Step 6 — Result parsing (no LLM).** Extract which tests failed and on what inputs.
- **Step 7 — Fix suggestion (LLM call #4, optional).** Propose a corrected function,
  verified by re-running the same tests; labeled "AI suggestion — review carefully."

## 4. Build phases & exit criteria

- **Phase 1 — Foundation.** ✅ Done. FastAPI service; `POST /v1/analyze` parses a function
  and returns AST metadata. *Exit: paste a function, get JSON with name/args/types/docstring.*
- **Phase 2 — Property Detection.** ✅ Done. LLM detects property classes;
  `/v1/analyze` returns them alongside the AST; structured output + retry (Instructor); low
  temperature; few-shot prompt; golden set; tracing. *Exit: `/v1/analyze` returns detected
  properties (each with a testable relation + confidence) alongside the AST; detections look
  reasonable for known patterns; traces visible.* The golden set deliberately includes 2–3
  plain business-logic functions (the kind that would otherwise get only `totality`) to
  confirm `value_postcondition` catches them — closing the coverage gap inside Phase 2.
- **Phase 3 — Strategy Generation.** ✅ Done. Second LLM call: detected properties + types →
  Hypothesis strategies. *Exit: strategies compile standalone and produce `@composite` code.*
- **Phase 4 — Test File Generation.** ✅ Done. Third LLM call → complete, self-contained pytest
  file asserting the relations; returned from `/v1/analyze` as `test_file`. *Exit: generated files
  run under pytest (pass = clean, fail = bug) without crashing at collection.*
- **Phase 5 — Kestrel Integration.** ✅ Done. Thin `kestrel.py` `/execute` client
  (D37) + sandbox execution (D38) + result parsing (D39/D40); `/v1/analyze` runs the generated tests
  in the sandbox and returns `bugs_found` (D41), with a 504 for timed-out runs (D42). Custom
  runtime image `docker/test-runtime.Dockerfile` (python:3.12-slim + pinned pytest/hypothesis, D43).
  Verified by a live end-to-end smoke against a running Kestrel (real bugs with failing inputs; the
  smoke also caught + fixed a `results.py` pytest-`E`-prefix parsing bug).
  *Exit: `/v1/analyze` returns bugs with failing inputs.* ✅ met.
- **Phase 6 — Fix Suggestions.** ✅ Done. Fourth LLM call (`fixgen.suggest_fix`, opt-in via
  `include_fix_suggestion`, D44): propose a corrected function, **verify it by re-running the SAME
  generated tests** against it in the sandbox (D45) — `verified=true` only when that re-run is green,
  else "no confident fix" (`verified=false`). The fix step is best-effort: any failure degrades
  `fix_suggestion` rather than failing the request (D44). *Exit: a verified fix for ~60%+ of detected
  bugs on the golden set.* ✅ met — the live fix smoke produced verified fixes for **4/4** detected
  golden-set bugs (100%), with no false positive on the correct control.
- **Phase 7 — GitHub App.** ✅ Done. `POST /webhook/github` verifies the signature and enqueues onto an **arq/Redis** queue (D46/D47);
  a separate **worker** mints an installation token, pulls the PR's changed `.py` files, extracts the
  changed top-level functions from the diff, runs each through the existing pipeline (+ a verified fix),
  and posts **one summary comment** (D48). Best-effort per function; comments only when bugs are found.
  Postgres deferred (installation tokens are minted on demand; D1). *Exit: install on a test repo, open a
  buggy PR, see a comment within 2 min.* ✅ met — live: a buggy `absolute` PR got a `[bot]` comment with 2
  property violations + a verified fix in ~24s of analysis.
- **Phase 8 — Web Demo.** 🔄 In progress. A single self-contained page (inline CSS + vanilla JS, no
  build step, no external assets) served at `GET /` by the API itself (D49), POSTing to `POST /v1/analyze`
  on the same origin with `include_fix_suggestion` — it renders the detected properties, each failing
  input, and the verified fix. Unit 2 (D50) persists each run to SQLite and adds `GET /v1/runs/{id}` for
  shareable links; per-IP rate-limiting is deferred (Phase 9 owns limits). *Exit: public URL; a recruiter
  pastes a function and sees bugs in ~60s.*
- **Phase 9 — Observability, Cost Controls, Hardening.** 🔄 In progress. Full tracing, per-install/per-IP
  rate limits, per-function cost budget. **Unit 1 (D51) done:** `AnalyzeResponse.metadata` is now real —
  `analysis_duration_ms`, `llm_cost_usd` (summed LiteLLM cost, metered at the `llm.complete` chokepoint via a
  request-scoped `cost_scope()` contextvar), `tests_generated`, `tests_run`, and `hypothesis_examples_tried`
  (null pending a Hypothesis stats hook). **Unit 2 (D52) done:** a per-analysis cost budget — config
  `max_cost_usd` (default $0.50, hard cap; a request's `max_cost_usd` can only lower it), enforced at the
  cost meter; an analysis that crosses it aborts with **402** (the fix step degrades instead). **Unit 3
  (D53) done:** rate limiting — per-IP on `/v1/analyze`, per-installation on the webhook (fixed-window,
  **429** + `Retry-After`), behind a `RateLimiter` seam with an in-memory default and a Redis-backed
  backend flippable via `rate_limit_backend`. **Unit 4 (D54) done:** per-analysis tracing — `trace_scope` +
  `span` emit one structured summary log per analysis (per-stage timeline + cost + bugs + duration,
  correlated by `analysis_id`), with an optional `log_format=json` for aggregators and a seam keeping
  Langfuse/OTel a drop-in. **Unit 5 (D55) done:** hardening edges — a Kestrel transport error or transient
  status (429/502/503/504) now surfaces as **503** + `Retry-After` (a `SandboxUnavailableError`, not folded
  into 500), and `code` is capped at 100k chars → **422** up front. ✅ **All 5 units done** — the cost,
  rate, observability, and resilience controls are in place. *Exit: production-ready under load.*
- **Phase 10 — Polish & launch.** Docs, acknowledgments, final demo.

## 5. API specification

### POST /v1/analyze (public)

**Request**
```json
{
  "code": "def parse_version(v: str) -> tuple[int, int, int]: ...",
  "function_name": "parse_version",
  "model_tier": "standard",
  "include_fix_suggestion": true,
  "max_test_runtime_seconds": 30
}
```
`function_name` optional (inferred when the source has one function). `model_tier` is one
of `economy` / `standard` / `premium` (unknown → standard). `max_test_runtime_seconds` is
the per-run sandbox budget in seconds (Phase 5; falls back to the configured default, and
Kestrel clamps it to its own ceiling). `include_fix_suggestion` (Phase 6, D44) defaults to **false**;
set it true to also get a verified `fix_suggestion` when bugs are found — opt-in because it adds an LLM
call plus a second sandbox run.

**Response** (fields appear only once their phase makes them real — D5)
```json
{
  "analysis_id": "uuid-...",
  "function": { "name": "...", "signature": "...", "args": [], "return_type": "...", "docstring": "..." },
  "properties": {
    "detected": [
      {
        "property_class": "round_trip",
        "relation": "parse_version(format_version(x)) == x",
        "companion_function": "format_version",
        "rationale": "parse/format inverse pair",
        "confidence": 0.95
      }
    ],
    "input_types": { "v": "str" },
    "return_type": "tuple[int, int, int]"
  },
  "strategy_plan": {
    "strategies": [
      { "argument": "v", "strategy": "st.text()", "rationale": "any string is valid input to a parser", "confidence": 0.7 }
    ],
    "extra_imports": []
  },
  "test_file": {
    "source": "from hypothesis import given, strategies as st\nimport pytest\n\n\ndef parse_version(v): ...\n\n\n@given(v=st.text())\ndef test_round_trip(v): ...",
    "test_names": ["test_round_trip"],
    "skipped": []
  },
  "bugs_found": [ { "test_name": "test_totality", "failing_input": "v=''", "error": "IndexError", "violated_property": "first_char(v) does not raise", "severity": "crash" } ],
  "fix_suggestion": { "code": "...", "verified": true, "tests_passed": 47, "tests_failed": 0 },
  "metadata": { "analysis_duration_ms": 24180, "llm_cost_usd": 0.0123, "tests_generated": 2, "tests_run": 2, "hypothesis_examples_tried": null }
}
```

`metadata` is populated as of Phase 9 Unit 1 (D51): timing + summed LLM cost + test counts.
`hypothesis_examples_tried` is `null` until a Hypothesis-statistics hook is added in the sandbox —
honest-null rather than a fabricated count (D5/D40).

**Status codes:** 200 (analysis complete; `bugs_found` may be empty) · 400 (code doesn't
parse, or `function_name` not found) · **402 (exceeded the `max_cost_usd` budget — body carries
`spent_usd`/`limit_usd`, D52)** · 429 (rate limit) · 500 (pipeline failure — body
includes the failing `stage`) · **503 (sandbox temporarily unavailable — `Retry-After`, D55)** ·
504 (exceeded `max_test_runtime_seconds`). `code` is capped at 100,000 chars (→ 422, D55).

`max_cost_usd` (optional request field, Phase 9 D52) is the per-analysis LLM-cost ceiling in USD;
it can only **lower** the server's configured cap (`min(request, config)`), and crossing it aborts
the analysis with 402.

### Other endpoints
- `POST /webhook/github` (internal) — GitHub `pull_request` events; HMAC-SHA256 signature-validated on
  the raw body (D47); **202** with the work enqueued onto arq/Redis (200 for ignored events, 403 bad
  signature). A separate worker analyzes the PR's changed functions and comments (D46/D48).
- `GET /` (public) — the web demo: a self-contained paste-a-function page that POSTs to `POST /v1/analyze`
  and renders the bugs + a verified fix (Phase 8, D49).
- `GET /v1/runs/{analysis_id}` (public) — fetch a previous analysis by id (shareable links); **200** or
  **404**. Each `POST /v1/analyze` run is persisted best-effort to SQLite behind a `RunStore` seam (Phase 8, D50).
- **Auth:** web demo unauthenticated + rate-limited per IP; GitHub App uses GitHub's JWT;
  future API consumers use an API key.

## 6. Tech stack

Python 3.11+ · FastAPI · **LiteLLM** (model gateway) · **Pydantic + Instructor**
(structured output + retry) · stdlib `ast` · **Hypothesis** + **pytest** (generated into
output) · **Kestrel** (sandbox) · PostgreSQL + Redis (added when a feature needs them) ·
Langfuse (tracing) · Next.js or HTML+HTMX (demo). Tooling: `uv`. Models: served through
**LiteLLM** (the model gateway), tiered economy / standard / premium; the concrete model ID
behind each tier is configured in `config.py`.

## 7. Deviations from the original PDF brief

- **Phase 2 is property-class DETECTION, not contract inference** (D23). The PDF's Step 2
  produced `{preconditions, postconditions, invariants}` inferred from the body; that is a
  circular oracle (tests re-encode the code, so they pass by construction and catch only
  crashes). Detecting well-known property classes gives implementation-independent oracles
  that catch silent wrong-answer bugs. Downstream steps (3–4) consume detected properties +
  testable relations instead of a contract. See `DECISIONS.md` D23–D25 for full rationale.
- Infrastructure (Postgres/Redis, docker-compose) is added when a feature first needs it,
  not upfront (D1, D12).

## 8. Known risks & planned refinements (Phase 2)

Property-class detection is a deliberate **precision-over-coverage** bet: more reliable and
executable than free-form contracts, but narrower. Three known weaknesses, recorded so the
trade-off stays visible while Phases 3–6 are built on this foundation:

1. **Coverage for non-taxonomy functions — closed inside Phase 2.** Plain business logic
   (`calculate_tax`, `apply_discount`, `clamp`) fits none of the relational classes and would
   otherwise get only the weak crash-only `totality`. The **value_postcondition** class
   (added in Phase 2, D26) closes this with an intent-derived constraint on the output value
   (`result >= 0`, `0 <= result <= price`). It is the most circular-prone class, so it carries
   the tightest discipline: the constraint must come from name/signature/docstring, never from
   the body, with low confidence when guessing. The Phase 2 golden set deliberately includes
   2–3 such business-logic functions to confirm the class catches them. This gap-closing stays
   in Phase 2 — it does not slip to later phases.

2. **Metamorphic / postcondition false positives.** A wrong inferred relation — most acutely a
   wrong `value_postcondition` — produces a test that fails against *correct* code: a phantom
   bug. For a PR bot, false alarms erode trust fast. *Guard (Phase 5/6 — recorded as a note,
   NOT built in Phase 2):* at report time, gate by confidence and treat a low-confidence
   postcondition failure as "possible issue — the property may be wrong," not "confirmed bug";
   verify a relation actually holds (re-run against the function on generated inputs) before
   surfacing any bug derived from it.

3. **Round-trip companion availability.** Round-trip — the most powerful class — needs the
   inverse function (`format_version` for `parse_version`), which often is not in the
   submitted snippet or the PR diff. *Handling:* `companion_function` is optional; when the
   companion is unavailable, round-trip degrades gracefully to "detected but not executable."
   Fetching the companion from the surrounding repo is a possible Phase 7 (GitHub diff)
   enhancement.

# TypeWright — Decisions Log

This file records non-obvious engineering decisions and *why* they were made.
One entry per decision. Newest phase at the bottom.

## Phase 1 — Foundation

### D1 — Defer PostgreSQL and Redis until a feature needs them
**Decision:** Phase 1 ships the FastAPI app + AST parser only. No Postgres, no Redis.
**Why:** The Phase 1 exit criteria (parse a function, return JSON) exercises neither.
Installations and the background queue don't exist until Phase 7; run-history has no
consumer yet. Wiring an unused DB/queue is surface area with no behavior to show and
contradicts the brief's "simpler beats cleverer." Each store is added when a feature
first reads/writes it, so that feature defines the right schema.

### D2 — Packaging/deps tool: uv
**Decision:** Use uv for Python version, venv, deps, and lockfile.
**Why:** Single fast tool, 2025 default for new Python projects. Also matches Kestrel,
keeping the portfolio's tooling story consistent.

### D3 — src-layout (`src/typewright/`)
**Decision:** Use src-layout rather than a flat package.
**Why:** Prevents importing the uninstalled package during tests; standard for
distributable packages (typewright and later kestrel-client are distributed).

### D4 — Endpoint path `/v1/analyze`
**Decision:** Version the path from day one.
**Why:** Matches the documented API spec (§7.1); avoids ever renaming a public path.

### D5 — Honest response subset, not a full envelope with empty fields
**Decision:** Phase 1 returns `{ analysis_id, function: {...} }`. Later phases add
`contract`, `bugs_found`, etc. as they become real.
**Why:** Shipping `bugs_found: []` before any testing exists falsely implies "we looked
and found none." Each field appears only when it's truthful.

### D6 — Extract rich metadata internally, expose a lean subset
**Decision:** One internal `FunctionMetadata` model captures name, structured args
(name/type/default/kind), return type, docstring, is_async, decorators, reconstructed
signature, and raw source. The Phase 1 response exposes name, signature, args, types,
docstring.
**Why:** Phase 2 (contract inference) needs the full signature and body; extracting it
now avoids re-touching the parser later. Signature string built via `ast.unparse()`.

### D7 — Phase 1 supports top-level functions only
**Decision:** Support top-level `def` / `async def`. Methods, nested functions, and
lambdas are out of scope for Phase 1, rejected cleanly.
**Why:** Not in the exit criteria; they add real edge cases. Revisit when the GitHub
diff path (Phase 7) needs methods.

### D8 — Explicit error→HTTP mapping
**Decision:** SyntaxError → 400; function_name not found → 400; multiple functions with
no name → 400; unexpected → 500.
**Why:** Matches §7.1 status codes for the paths that exist in Phase 1.

### D9 — pydantic-settings from the start
**Decision:** Env-driven `Settings` class even though Phase 1 configures little.
**Why:** LLM keys (Phase 2) and Kestrel config (Phase 5) are env-var driven; the
pattern is cheap to establish now and correct later.

### D10 — stdlib logging in Phase 1
**Decision:** Simple stdlib `logging` config. No structlog/Langfuse yet.
**Why:** Rich observability is an explicit Phase 2/9 concern; don't pull it forward.

### D11 — pytest example tests + optional single Hypothesis test
**Decision:** Example-based parser tests covering the exit criteria and chosen
edge cases; at most one Hypothesis "never crashes on valid source" test.
**Why:** Lean coverage of real behavior; heavier dogfooding of Hypothesis comes later.

### D12 — Dockerfile now, docker-compose deferred
**Decision:** Ship an app Dockerfile. Defer compose until there's a second container.
**Why:** Reproducible uvicorn run is useful immediately; compose with one service is
premature given D1.

### Meta — Python 3.12 (3.11 floor)
**Decision:** Target 3.12; minimum supported 3.11.
**Why:** Brief specifies 3.11+; 3.12 is a safe modern default. `ast.unparse` is 3.9+.

## Phase 2 — LLM Property Detection (was: Contract Inference — redirected by D23)

### D13 — Structured contract output via Instructor; API key passed explicitly
**Amended by D23:** mechanism (Instructor structured output + explicit key) still holds; the
model is now `PropertyDetection`, not `Contract`.
**Decision:** Get the `Contract` back as a validated Pydantic object using **Instructor**
(which re-asks the model, up to `llm_max_retries` times, when the output doesn't fit the
schema) rather than hand-parsing free-form text. Read the provider key from
`ANTHROPIC_API_KEY` (or `TYPEWRIGHT_ANTHROPIC_API_KEY`) into `Settings` and pass it
**explicitly** to the client.
**Why:** The contract has a fixed shape (D14); Instructor turns "model returns JSON-ish text"
into "model returns a `Contract`, or we retry", so the pipeline never has to defend against
malformed output. Passing the key explicitly (rather than relying on ambient global env) keeps
configuration in one typed place (D9) and makes the call testable — a test can inject a fake
key/client without mutating the process environment.

### D14 — Contract shape: preconditions / postconditions / invariants
**Superseded by D23/D24** — Phase 2 is property-class detection; the `Contract` shape is
removed. Kept for the record.
**Decision:** Model the inferred contract as three lists of plain-language strings —
`preconditions`, `postconditions`, `invariants` — matching the brief's API spec (§7.1).
**Why:** It's the shape the spec promises callers and the shape Phase 3 consumes to generate
Hypothesis strategies. Fixing it now means the LLM step, the API response, and the strategy
generator all agree on one structure. Plain strings (not a richer AST) keep Phase 2 simple and
the model's job well-defined; structure can be added later if a phase needs it.

### D15 — Internal failures use `PipelineError`, which names the failing stage
**Decision:** A failure in one of our own analysis steps raises `PipelineError(stage, detail)`
— an exception deliberately **outside** the `TypeWrightError` family — so it maps to 500, and
it carries the name of the stage that failed.
**Why:** D8 split errors into caller-fault (`TypeWrightError` → 400) and our-fault (→ 500) by
type. Contract inference is the first step that can fail without the caller doing anything
wrong, so it needs the 500 side — but a bare 500 is unhelpful. The brief says a 500 response
should report the failing stage (§7.1); carrying `stage` on the exception lets the HTTP layer
include it without inspecting messages.

### D16 — Conservative LLM call-tuning defaults
**Decision:** Default LLM call tuning lives in `Settings`: `llm_timeout_seconds = 30.0`,
`llm_max_retries = 2` (Instructor reasks on schema-invalid output), `llm_max_tokens = 1024`.
**Why:** Contracts are small, so a 1024-token ceiling is ample and keeps each call cheap; a 30s
timeout bounds latency so a slow provider can't hang a request; two reasks recover from the
occasional malformed model output without looping forever. Centralising them in `Settings` (D9)
means they're tunable per environment without code changes.

### D17 — LiteLLM as the model gateway, with three model tiers
**Decision:** Call models through **LiteLLM** using provider-prefixed IDs
(`anthropic/claude-...`). Expose `economy` / `standard` / `premium` tiers mapped to concrete
models in `Settings`, with `default_model_tier = "standard"` and `model_for_tier()` falling
back to the standard model for any unknown tier.
**Why:** TypeWright wants a tiered, swappable model story: callers (and the brief's `model_tier`
field) pick by intent — cost vs. capability — without hard-coding model IDs at call sites, and
the model behind each tier (or the provider itself) changes in one place. LiteLLM gives one
call shape across providers/models, so swapping a backend is a config change, not a code
change, and the `anthropic/` prefix is just how LiteLLM routes. Falling back to `standard`
means a stale or mistyped tier degrades gracefully rather than erroring. Trade-off: LiteLLM
sits a layer above any single provider's SDK, so provider-specific features (e.g. prompt
caching, adaptive-thinking controls) aren't first-class — revisit for the hot path if a
later phase needs them. Model IDs verified against the current catalog (Haiku 4.5 / Sonnet 4.6
/ Opus 4.8); the bare aliases are correct and must **not** carry date suffixes.

### D18 — The LLM layer lives in one `inference.py`
**Decision:** A single `inference.py` module owns both the Instructor-wrapped LiteLLM client
(built from `Settings`) and the `infer_contract(meta: FunctionMetadata) -> Contract` entry
point.
**Why:** Phase 2 makes exactly one kind of LLM call; one module is the fewest moving parts and
matches the brief's "simpler beats cleverer." Splitting client plumbing from orchestration (or
a dedicated `llm/` package) only earns its keep once a second caller exists — Phase 3
(strategy / test generation) is the likely trigger, and the split can happen then without
disturbing this unit.

### D19 — Phase 2 infers contracts in a single structured call (MVP)
**Decision:** Contract inference is one structured Instructor + LiteLLM call: send the function
source, get back a validated `Contract`, relying on D13's reask retries for malformed output.
No agentic draft → critique → refine loop yet.
**Why:** A single call is the smallest thing that produces a real contract and lets the rest of
the pipeline (Phase 3 onward) start consuming it. The agentic, multi-step approach from the
source research (arXiv:2510.09907) costs more tokens, latency, and code to test; defer it until
contract *quality* is shown to need it, and add it then as a measured improvement rather than
upfront complexity.

### D20 — Build inference standalone first; wire `/v1/analyze` in the next unit
**Decision:** This unit delivers `infer_contract` plus its tests in isolation — the model is
mocked so tests need no live API key. Evolving `AnalyzeResponse` to include `contract` and
wiring the route is a separate, following unit.
**Why:** Keeps units small and independently shippable (the Phase 1 cadence). Mocking the LLM
keeps the test suite fast, deterministic, and runnable in CI with no secret, and defers the
question of how the live key reaches CI to the unit that actually needs it. Same spirit as D12
(ship the smaller useful surface; defer the rest until a feature needs it).

### D21 — `/v1/analyze` runs contract inference; inference injected as a dependency
**Decision:** The endpoint now parses the function *and* infers its `Contract`, returning
`{ analysis_id, function, contract }` (D5 grows by one honest field). Contract inference is
reached through a FastAPI dependency (`get_infer_contract`) rather than called inline, and a
`PipelineError` maps to HTTP 500 with the failing `stage` in the body.
**Why:** This is the Phase 2 exit criterion — "POST /analyze returns contract JSON alongside
AST." Injecting the inference step gives tests a clean seam (`app.dependency_overrides`) so the
whole HTTP suite runs deterministically with no live LLM key, instead of monkeypatching module
internals — and it's the idiomatic FastAPI test seam. The 500+stage mapping is the §7.1 contract
for our-fault failures (D15); the handler reads `stage` off the exception so the body can report
it.

### D22 — Request gains only `model_tier`; other §7.1 fields deferred
**Decision:** `AnalyzeRequest` adds `model_tier` (the field Phase 2 actually uses). The other
request fields the spec lists — `include_fix_suggestion`, `max_test_runtime_seconds` — are not
added yet; Pydantic ignores them if sent.
**Why:** Same honesty rule as D5, applied to the request: don't advertise a knob that does
nothing. `model_tier` drives tier selection now (D17); fix-suggestion and runtime-budget belong
to the phases that implement them (6 and 5), and arrive then.

### D23 — Phase 2 is property-class DETECTION, not contract inference (supersedes D14)
**Decision:** Replace "infer preconditions/postconditions/invariants" with detecting which
well-known PROPERTY CLASSES a function fits — round-trip, idempotence, invariant-preservation,
metamorphic, type-postcondition, totality. The LLM reasons from name/signature/type
hints/docstring and RECOGNIZES classes rather than synthesizing a bespoke spec; it signals
uncertainty with low confidence instead of fabricating. No dual path — the contract approach is
removed.
**Why:** Inferring a spec from the body and testing the body against it is circular: the oracle
is the implementation, so tests pass by construction and catch only crashes. Property classes are
implementation-independent oracles (`parse(format(x)) == x` holds regardless of how `parse` is
written), so they catch silent wrong-answer bugs — the actual point of PBT. Recognition also
plays to LLM strengths (classification) over their weakness (precise spec synthesis). This
diverges from the brief's literal Step 2 but is more faithful to PBT fundamentals and the prior
art (arXiv:2510.09907). D13/D17/D18/D19 still hold; D21/D22 still hold with the response field
renamed `contract`->`properties` and the pipeline stage `contract_inference`->`property_detection`.
The maintained spec is now `PROJECT_BRIEF.md` (the PDF is the original record).

### D24 — Property-analysis data shape
**Decision:** `DetectedProperty` carries: `property_class` (enum), a concrete TESTABLE `relation`
(e.g. `parse(format(x)) == x`, not prose), optional `companion_function` (round-trip inverse),
`rationale`, and `confidence` (0–1, enforced). The LLM returns a `PropertyDetection` (list only);
we assemble `PropertyAnalysis` = detected list + AST-declared `input_types`/`return_type`.
**Why:** A testable relation is what Phase 3 turns straight into a Hypothesis test; confidence
lets later phases threshold/verify and is the anti-fabrication signal. Carrying the AST types in
the analysis makes it a self-contained payload for downstream phases (a small, deliberate overlap
with `function`). Keeping the LLM's output narrow (properties only) stops it echoing or
hallucinating types we already have from the AST.

### D25 — Low temperature + few-shot for detection
**Decision:** `llm_temperature = 0.0` (in `Settings`), and two few-shot examples in the system
prompt.
**Why:** Detection should be stable and reproducible, and low temperature curbs the model's
tendency to invent properties to look useful (reinforcing D23's confidence-not-fabrication rule).
Few-shot examples anchor the output format (testable relation, companion function, confidence)
better than instructions alone.

### D26 — value/output-postcondition class, on the tightest leash
**Decision:** Add a `value_postcondition` property class: a testable constraint on the output
VALUE (e.g. `result >= 0`, `0 <= result <= price`, a probability in `[0, 1]`). The constraint
must be derived from the function's INTENT (name/signature/docstring), never from what the body
computes; the model emits it with low confidence when guessing and is instructed to omit it
rather than fabricate. It is preferred over `totality`. The Phase 2 golden set includes 2–3 plain
business-logic functions to confirm the class catches them — gap-closing stays inside Phase 2.
**Why:** The relational classes (round-trip/idempotence/invariant/metamorphic) don't cover plain
business logic (`calculate_tax`, `apply_discount`, `clamp`), which would otherwise get only the
weak crash-only `totality`. A value postcondition is a real, executable, implementation-
independent oracle for those — but ONLY if it comes from intent, not the body, else it is exactly
the circular oracle D23 rejects. It is the most powerful class and the most fabrication-prone,
hence the tightest leash. The report-time false-positive guard for a wrong postcondition (a
phantom bug against correct code) is recorded in `PROJECT_BRIEF.md` §8 as a Phase 5/6 concern,
deliberately not built in Phase 2.

## Phase 3 — Strategy Generation

### D27 — Strategy generation lives in a new `generation.py`; the LLM client is shared via `llm.py`
**Decision:** Phase 3's strategy generation is a new module `generation.py`
(`generate_strategies`), not more code bolted onto `inference.py`. The Instructor-wrapped LiteLLM
client construction is lifted out of `inference.py` into a shared `llm.py` (`build_client()`);
both `inference.py` and `generation.py` keep a thin module-level `_client()` that delegates to it.
**Why:** This is exactly the split D18 deferred to "the Phase 3 (strategy / test generation)
trigger." Now that a *second* LLM caller exists, the provider/Instructor wiring belongs in one
place, and a module per analysis step keeps each focused (detection vs. generation). Keeping each
module's own `_client()` seam means the existing inference tests (which monkeypatch
`inference._client`) stay green and Phase 2 is undisturbed — D18's own caveat. The common *call*
shape (the `create(...)` kwargs + `PipelineError` wrapping) is left duplicated across the two
modules for now; it can be hoisted into `llm.py` once a third caller (Phase 4 test-gen) proves the
pattern. Simpler beats cleverer until the duplication actually bites.

### D28 — Phase 3 Unit 1 is standalone; `/v1/analyze` wiring is a later unit
**Decision:** Unit 1 ships `generate_strategies` plus its mocked tests only; the endpoint is not
touched. Evolving `AnalyzeResponse` to carry the strategies (and calling generation from the
route) is a separate, following unit.
**Why:** Same cadence as D20 in Phase 2 — small, independently shippable units, with the LLM
mocked so the suite stays fast, deterministic, and key-less in CI. Per the D5 honesty rule the
response shape changes only when the wiring unit makes the field real, so the public API and the
`PROJECT_BRIEF.md` spec are untouched by this unit.

### D29 — Strategy-generation output shape: a `StrategyPlan` of `GeneratedStrategy`, returned as-is
**Decision:** `generate_strategies` returns a `StrategyPlan` = a list of `GeneratedStrategy`
(`argument`, a `strategy` *expression* like `st.integers()`, `rationale`, a range-checked
`confidence`) plus `extra_imports`. The LLM produces the whole `StrategyPlan` in one structured
call (`response_model=StrategyPlan`) at low temperature with few-shot, and the function returns it
**directly** — no AST bolt-on.
**Why:** A per-argument strategy *expression* is what Phase 4 drops straight into
`@given(arg=<strategy>)`, and `extra_imports` carries anything beyond `from hypothesis import
strategies as st`. The shape deliberately mirrors Phase 2's `DetectedProperty`/`confidence`
pattern (D24) so the two LLM steps read alike, and `confidence` is the same anti-fabrication /
thresholding signal. Unlike `infer_properties` (which merges AST types into its result),
generation has no extra AST facts to add — the strategies *are* the generated content — so it
returns the model's `StrategyPlan` unchanged. A coverage check (assert every argument got a
strategy) is a deliberate later refinement, not in the MVP.

### D30 — `/v1/analyze` runs the full chain; response gains `strategy_plan`; always-on, all-or-nothing
**Decision:** The endpoint now parses → detects properties → **generates strategies**, returning
`{ analysis_id, function, properties, strategy_plan }`. Strategy generation is reached through a
`get_generate_strategies` FastAPI dependency (mirroring `get_infer_properties`, D21) and runs
**unconditionally** on every request — no opt-out flag. The request's `model_tier` drives **both**
LLM calls. Failure handling stays **all-or-nothing**: any stage raising `PipelineError` → 500
naming the stage (D15), so a generation failure after a successful detection still 500s rather
than returning a partial result. The new response field is **`strategy_plan`** (a `StrategyPlan`),
not `strategies`.
**Why:** This is the Phase 3 wiring increment D28 deferred. Always-on keeps the endpoint's
contract simple — "analyze" means the whole pipeline — and matches the brief's deterministic-chain
model; the extra LLM call's cost/latency is an explicit Phase 9 concern, not a reason to add a knob
now (D22). All-or-nothing keeps the response honest (no half-analysis masquerading as complete) and
reuses the existing `PipelineError`→500+stage contract. The dependency seam keeps the HTTP suite
key-less (D21). The field is named `strategy_plan` because naming it `strategies` would nest as
`strategies.strategies` in the JSON (the `StrategyPlan`'s own list is `strategies`); `strategy_plan`
reads cleanly beside `properties` (whose inner field is `detected`). This field was **not** in the
brief's §5 response (strategies had been left as an internal artifact); exposing it is a small,
deliberate extension recorded in `PROJECT_BRIEF.md` §5, consistent with the D5 "expose once real"
rule.

## Phase 4 — Test File Generation

### D31 — Test generation lives in a new `testgen.py`; the shared LLM *call shape* hoists into `llm.py`
**Decision:** Phase 4's test-file generation is a new module `testgen.py` (`generate_test_file`),
mirroring the module-per-step pattern (D27). With this *third* LLM caller, the call shape that the
three steps repeated verbatim — the `create(...)` kwargs plus the `PipelineError` wrapping — is
hoisted into a shared `llm.complete(client_factory, *, stage, settings, model, response_model,
messages)`. `inference.py`, `generation.py`, and `testgen.py` each keep their thin module-level
`_client()` and **pass it (uncalled) into `complete()`**, which does the API-key check first, then
the call, then wraps any failure as `PipelineError(stage, …)`.
**Why:** D27 explicitly named "a third caller (Phase 4 test-gen)" as the trigger to hoist the call
shape "once the pattern is proven" — this is that caller, and three copies is the rule-of-three
point to deduplicate. Passing each module's `_client` factory into `complete()` **preserves the
per-module test seam**: every existing test that monkeypatches `inference._client` /
`generation._client` still controls the client, so the Phase 2/3 suites stayed green untouched.
The key-check runs before building the client (the same order as before the hoist), and
`max_retries`/`max_tokens`/`temperature`/`timeout` are read from `settings` since all callers used
identical values.

### D32 — Hybrid assembly: the LLM writes only the test functions; `testgen.py` assembles the file
**Decision:** The LLM returns just the per-property `@given` test functions (a `GeneratedTests`);
`testgen.py` then **deterministically assembles** the final file = import header + extra imports +
the function under test + the LLM's tests. The model never emits the imports or re-emits the
function. Chosen over "LLM emits the whole file as one string."
**Why:** This puts the parts that *must* be right (the import header, the verbatim function under
test) under our control and asks the model only for the genuinely creative bit — turning each
`relation` into a `@given` assertion. It is the same "merge in deterministic facts" philosophy as
`infer_properties` (D24), and it removes the worst failure mode of whole-file generation: the model
re-stating a **drifted or hallucinated** function body, so the tests would run against the wrong
code. Imports are order-preserving deduped (base → strategy `extra_imports` → test `extra_imports`)
so a module needed by both a strategy and a test appears once.

### D33 — Output is a self-contained file: prepend the function source so it runs under pytest now
**Decision:** The generated file is **self-contained** — `testgen.py` prepends the function's own
source (`meta.source`, which the parser already holds) ahead of the tests — rather than a test-only
file that imports the function from elsewhere.
**Why:** The Phase 4 exit criterion is "generated files run under pytest without crashing at
collection." A self-contained file satisfies that *today*, standalone, and matches the brief's
"combined file (function + tests)" execution model (§3 Step 5). Because we already own the exact
function source, prepending it deterministically is strictly safer than asking the model to
reproduce it (see D32). The companion problem for round-trip — when the inverse isn't in the
snippet — is handled by `skipped`, not by importing a function that may not exist (PROJECT_BRIEF §8
risk 3).

### D34 — Validate with `ast.parse()` only, in-process; defer dry-run import/execution to Kestrel
**Decision:** Phase 4's only validation gate is a static `ast.parse()` of the assembled file; a file
that doesn't parse raises `PipelineError("test_generation", …)` → 500. We deliberately do **not**
import or execute the generated code inside the TypeWright process. The brief's "ast.parse +
**dry-run import**" is split: `ast.parse` here, real import/execution in the Kestrel sandbox
(Phase 5).
**Why:** Importing generated code runs it, and running untrusted generated code in-process is exactly
the risk Kestrel exists to contain — doing it in the API process would undo the architecture's core
safety boundary (§2). `ast.parse` is a cheap, deterministic, side-effect-free check that catches the
realistic failure (a malformed test function) and meets the exit criterion ("doesn't crash at
collection") without executing anything. This refines, not contradicts, the brief — recorded in
`PROJECT_BRIEF.md` §3 Step 4.

### D35 — Two models (`GeneratedTests` raw → `GeneratedTestFile`); test names off the AST; Unit 1 standalone
**Decision:** The LLM's `response_model` is `GeneratedTests` (`test_functions`, `extra_imports`,
`skipped`); `generate_test_file` returns `GeneratedTestFile` (`source`, `test_names`, `skipped`).
`test_names` are read off the **parsed AST** (top-level `def test_*`), not trusted from the model.
A property the model can't make executable (e.g. round-trip with an absent companion) goes into
`skipped` with a reason. Per the D20/D28 cadence, Unit 1 ships the standalone module + mocked tests
only; wiring `/v1/analyze` (response gains `test_file`) is a following unit.
**Why:** Splitting the raw LLM output from the returned artifact keeps the hybrid-assembly seam
clean (D32) — the model owns the tests, `testgen.py` owns the file. Reading `test_names` from the AST
makes them a *fact about the assembled file* rather than a claim the model could get wrong. `skipped`
keeps the result honest about what wasn't covered (PROJECT_BRIEF §8 risk 3) instead of silently
dropping properties. Standalone-first matches every prior phase (D20, D28): small, independently
shippable, suite stays fast and key-less; the response shape changes only when the wiring unit makes
`test_file` real (the D5 honesty rule), so the public API and `PROJECT_BRIEF.md` §5 are untouched by
this unit.

### D36 — `/v1/analyze` runs the full four-step chain; response gains `test_file`; always-on, all-or-nothing
**Decision:** The endpoint now parses → detects properties → generates strategies → **generates the
test file**, returning `{ analysis_id, function, properties, strategy_plan, test_file }`. Test
generation is reached through a third `get_generate_test_file` FastAPI dependency (mirroring
`get_infer_properties`/`get_generate_strategies`, D21/D30) and runs **unconditionally**; the request's
`model_tier` drives all three LLM calls. Failure handling stays **all-or-nothing**: any stage raising
`PipelineError` → 500 naming the stage (D15), so a test-generation failure 500s rather than returning a
partial result. The new response field is **`test_file`** (a `GeneratedTestFile`), and a 200 always
carries a full one.
**Why:** This is the Phase 4 wiring increment D35 deferred. Always-on keeps the endpoint's contract
simple — "analyze" means the whole pipeline — and matches the brief's deterministic-chain model; the
extra LLM call's cost/latency is an explicit Phase 9 concern (D22), not a reason to add a knob now.
**Graceful degradation was considered and rejected** (the alternative on the table): returning a 200
with `test_file: null` when only test generation fails would keep the earlier stages' output, but it
makes `test_file` nullable and lets a "complete-looking" 200 silently omit the file — a softer, less
honest contract for no real gain at this stage. All-or-nothing instead reuses the existing
`PipelineError`→500+stage handler unchanged and matches D30. The dependency seam keeps the HTTP suite
key-less (D21). `test_file` is the obvious name beside `properties` and `strategy_plan`. Recorded in
`PROJECT_BRIEF.md` §5 (the D5 "expose once real" rule), the second field that rule has added to the
response after `strategy_plan`.

### D37 — Talk to Kestrel via a thin internal `/execute` client, not the shipped SDK (Phase 5, Unit 1)
**Decision:** TypeWright reaches the Kestrel sandbox through a small internal client, `src/typewright/kestrel.py`:
a ~40-line httpx wrapper over the stateless `POST /execute` endpoint only. It returns a frozen
`SandboxResult` dataclass mirroring Kestrel's `ExecuteResponse` field-for-field (`stdout`, `stderr`,
`exit_code`, `duration_ms`, `timed_out`, `stdout_truncated`, `stderr_truncated`). Auth is
`Authorization: Bearer <key>` (header omitted when `kestrel_api_key` is `None`, since Kestrel runs
auth-off locally); the per-run `timeout_seconds` is sent in the body, and the httpx read timeout is set
to that budget **+ `kestrel_http_timeout_buffer_seconds`** so the HTTP call outlives a legitimately long
run instead of aborting it client-side. Only a transport/HTTP failure raises — as
`PipelineError(stage="sandbox_execution")` → 500; a *timed-out* run comes back as data (`timed_out=True`),
mirroring Kestrel's own "timeout is data, not an error" contract. `run_in_sandbox` is the call seam; the
`_client()` factory is the test seam (monkeypatched with an `httpx.MockTransport`, so the suite needs no
live Kestrel). `httpx` moves from a dev-only to a declared runtime dependency, because this module imports
it directly (it was previously only transitive via litellm).
**Why:** The shipped `kestrel_client` SDK (v0.8.0, in the Kestrel repo) is real and mature, but depending
on it is awkward here: it is not published to a registry (a path dep is machine-specific; a git-subdir dep
couples our build to the Kestrel repo being reachable), and almost all of it — sessions, streaming, rich
outputs — is surface we never touch. A one-endpoint wrapper keeps TypeWright self-contained and buildable
in Docker/CI with no Kestrel checkout present, and reuses the established `_client()` seam idiom from the
LLM modules. The contract was verified directly against the Kestrel server source, not just the SDK/docs:
`ExecuteRequest`/`ExecuteResponse` (`api/schemas.py`), the `HTTPBearer` scheme and auth-disabled path
(`api/auth.py`), and the route returning a 200-with-`timed_out=True` (only raising on a real executor
error) and clamping `timeout_seconds` down to the server ceiling (`api/routes.py`). The cost is a little
duplicated request/response code for the `/execute` slice — small and worth the decoupling. (Carried to
Unit 3: a Kestrel `429`/`Retry-After` currently folds into a 500; the 100,000-char `code` cap can 422 a
pathological file — both to be handled when the endpoint is wired.)

### D38 — Add the Kestrel sandbox preamble at execution time, not in the Phase 4 output (Phase 5, Unit 1)
**Decision:** The sandbox-only preamble — `os.chdir("/tmp")`, a **database-less and deadline-less**
Hypothesis profile, and a `if __name__ == "__main__": sys.exit(pytest.main([__file__, "-q", "-p",
"no:cacheprovider"]))` runner — is wrapped around the test file at execution time by
`execution.wrap_for_sandbox`, NOT baked into the Phase 4 `GeneratedTestFile.source`. The Phase 4 output
stays a clean, self-contained pytest file (its contract from D33/D36 is untouched); `execution.run_tests`
wraps then submits via `run_in_sandbox`.
**Why:** Separation of concerns along the existing boundary (§2): testgen produces a *portable* test file a
developer can still run locally with `pytest`, and the sandbox layer owns the bits that only make sense
inside Kestrel. Baking the preamble into the Phase 4 file (the alternative considered) was rejected: it
would make `test_file.source` sandbox-specific — `os.chdir("/tmp")` fails on a normal machine — and reopen
the Phase 4 contract and its tests for no gain. The preamble's specifics follow Kestrel's read-only-cwd
execution model (`running-test-workloads.md` §4): `/tmp` is the one writable path; `database=None` stops
Hypothesis persisting its example DB under the read-only cwd; **`deadline=None`** stops the constrained
1-CPU sandbox's slowness from masquerading as a property failure; and Kestrel runs `python main.py` (no
pytest entrypoint), so the file must drive pytest itself, its process exit code becoming pytest's. Per the
D20/D28/D35 cadence this is Unit 1 (standalone capability + mocked tests); wiring `/v1/analyze` to return
`bugs_found` is a later unit, so the public API and `PROJECT_BRIEF.md` are untouched here.

### D39 — Parse sandbox results by text-scraping pytest/Hypothesis's stable markers (Phase 5, Unit 2)
**Decision:** A new `src/typewright/results.py` turns a `SandboxResult` into a `BugReport` with no LLM
and no sandbox-side plugin: it scrapes two markers straight from the captured stdout/stderr — pytest's
short-summary lines (`FAILED <nodeid>::<test_name> - <message>`), which give the authoritative set of
failed tests plus the crash message, and Hypothesis's `Falsifying example: test_x(<args>)` blocks, which
give the failing input. The args are read with a **balanced-bracket scan** (tracking `()[]{}` and
skipping brackets inside string literals) so a multi-line or nested example survives, then normalised to
one line. Each failed `test_<property_class>[_n]` is mapped back to the detected property's `relation`
(strip the `test_` prefix and any `_n` repeat suffix → the n-th detected property of that class) so
`violated_property` carries the relation, not just a class name. A timed-out run and a clean run both
yield zero bugs; a safety net builds best-effort bugs from falsifying examples if the short summary is
ever absent (a non-default pytest setup). `parse_results(result, analysis)` is a plain function (no seam
needed — it's pure, LLM-free, and unit-tested directly on real-shaped output).
**Why:** The alternative considered was a self-owned pytest plugin injected into the sandbox preamble
that emits structured JSON to stdout (the parser would then `json.loads` it). It is more robust to output-
format drift, but it runs extra custom code inside the *untrusted* execution path and adds moving parts to
the file we execute — and crucially the failing **input** still has to be scraped from Hypothesis's
`Falsifying example` text even with JSON, so JSON's marginal benefit is small. The `FAILED …` summary and
`Falsifying example:` markers have been stable across pytest/Hypothesis versions for years, and pytest
shows the short failure summary by default — so text-scraping meets the Phase 5 exit criterion (bugs with
failing inputs) with the least code and the simplest sandbox file. The JSON-hook route is recorded as the
hardening path (Phase 9) if scraping ever proves flaky. Step 6 of PROJECT_BRIEF §3 ("Result parsing — no
LLM") is exactly this.

### D40 — Bug shape + two-way severity (`crash` vs `property_violation`) (Phase 5, Unit 2)
**Decision:** `models.py` gains `BugSeverity` (`crash`, `property_violation`), `Bug`
(`test_name`, `failing_input`, `error`, `violated_property`, `severity`), and `BugReport`
(`bugs`, `timed_out`, `exit_code`, `tests_passed`, `tests_failed`, `output_truncated`). Severity is
**two-way**: a failure whose pytest message is a rewritten assertion (`assert …`) or an `AssertionError`
is a **`property_violation`** (an asserted relation failed = a silent wrong answer), reported with
`error="AssertionError"`; any other uncaught exception is a **`crash`** whose `error` is the leading
exception-type token (e.g. `IndexError`). `Bug` carries `test_name` (one field beyond the brief's four —
real and useful for traceability, allowed by D5 which forbids only *promised-but-unreal* fields); the
brief's single-value `failing_input` example is generalised to the full args text (`v=''`, `x=0, y=3`).
`BugReport` is the internal return type; the API will surface only `bugs` (as `bugs_found`) in Unit 3.
**Why:** The two-way split is the meaningful one for this tool: "the function returned a wrong answer"
(the property-detection thesis from D23 — silent bugs, not just crashes) versus "the function threw". A
third `error` bucket (for tests that error without a falsifying example) was considered and rejected for
an early phase — fuzzier category, larger surface; an errored test simply produces no bug and is visible
via `exit_code`. Detecting severity from the message (not just the FAILED line's first token) is required
because pytest renders a bare `assert` failure *without* an `AssertionError:` prefix, so keying on the
first token alone would misclassify the common case as a crash. `BugReport`'s extra fields (`timed_out`,
counts, truncation) are what Unit 3 needs to choose 504 and fill the brief's future `metadata` without a
second pass over the output. Per the standalone-first cadence (D20/D28/D35) this unit adds the models and
parser only; `AnalyzeResponse` and `PROJECT_BRIEF.md` §5 stay untouched until Unit 3 makes `bugs_found`
real.

### D41 — `/v1/analyze` runs the tests in the sandbox; response gains `bugs_found`; seam at the I/O boundary (Phase 5, Unit 3)
**Decision:** The endpoint now runs the full chain parse → detect properties → generate strategies →
generate test file → **run in the sandbox**, returning `{ analysis_id, function, properties, strategy_plan,
test_file, bugs_found }`. The sandbox step is reached through a fourth `get_run_tests` FastAPI dependency
(mirroring `get_infer_properties`/`get_generate_strategies`/`get_generate_test_file`, D21/D30/D36) — but
the seam sits at the **I/O boundary only**: it injects `execution.run_tests` (which calls Kestrel and
returns a raw `SandboxResult`), and the route then calls the *pure* `results.parse_results` itself. The
request gains `max_test_runtime_seconds` (optional; the route resolves the budget as the request value or
`settings.kestrel_timeout_seconds`, the new config default); `bugs_found` is `list[Bug]` (empty when every
property held). Failure handling stays **all-or-nothing**: a `PipelineError` from any LLM stage or the
sandbox call → 500 naming the stage (D15/D37).
**Why:** Mocking only the external call and running the real parser through the API means the HTTP suite
verifies that wiring + parsing actually agree end-to-end — a combined `find_bugs` seam (run + parse behind
one injectable) was considered and rejected because it would mock the parser away, so API tests could never
catch a wiring/parsing mismatch and parsing would need separate coverage. It also matches the codebase
principle that seams exist for I/O, not pure functions (`parse_results` is deterministic and side-effect-free,
so it needs no seam). Always-on keeps the contract simple ("analyze" means the whole pipeline, through
execution) and matches D30/D36. `bugs_found` is the brief's documented field, now made real per the D5
"expose once real" rule — the third field that rule has added to the response after `strategy_plan` and
`test_file`. (Deferred: a Kestrel `429`/`Retry-After` currently folds into the 500; the 100,000-char `code`
cap can 422 a pathological file — both noted for Phase 9 hardening.)

### D42 — A timed-out sandbox run returns 504 via `SandboxTimeoutError`, not a 200 (Phase 5, Unit 3)
**Decision:** When the `SandboxResult` comes back with `timed_out=True` (the tests exceeded the budget;
Kestrel killed the run and returned it as data, D37), the route raises a new `SandboxTimeoutError`, which a
dedicated handler maps to **504** with the budget in the detail. `SandboxTimeoutError` is deliberately
neither a `TypeWrightError` (the caller's input was valid) nor a `PipelineError` (no stage *failed* — the
tests ran, they just didn't finish), so it gets its own type + handler rather than folding into the 400/500
families.
**Why:** The alternative considered — return 200 with an empty `bugs_found` — was rejected because a
timed-out run carries *no information* about whether the function is correct, yet would read identically to
"analyzed cleanly, no bugs found." For a tool whose entire value is *trustworthy* bug reports, that false
all-clear is the worst possible failure mode. 504 is also exactly what PROJECT_BRIEF §7.1 specifies for an
exceeded `max_test_runtime_seconds`. The distinct exception type keeps the status-code mapping a single
`isinstance`/handler dispatch at the edge, consistent with the D8/D15 error-to-status design.

### D43 — The custom test-runtime image: Python 3.12 for parity, pinned-minimal deps, no USER/WORKDIR/CMD (Phase 5)
**Decision:** Add `docker/test-runtime.Dockerfile` — the image Kestrel runs each generated property-test file
inside. It is `FROM python:3.12-slim` with `pip install --no-cache-dir pytest==9.0.3 hypothesis==6.155.2`
(the exact `uv.lock` versions) and nothing else, `ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1`, plus a
build-time `python -c "import pytest, hypothesis"` smoke that fails the build if the install is broken. It sets
**no `USER`, `WORKDIR`, or `CMD`**, because Kestrel's executor drives the container as `docker run --user
65534:65534 --read-only --tmpfs /tmp:size=64m --workdir /sandbox <image> python /sandbox/main.py` — those three
would be overridden. The sandbox preamble (`os.chdir("/tmp")`, DB-less Hypothesis profile, `__main__` runner)
is **not** baked in here; `execution.py` adds it at run time (D38). Tagged `typewright-test-runtime:0.1`; Kestrel
is pointed at it with `KESTREL_EXECUTOR_DOCKER_IMAGE`.
**Why:** *Python 3.12 (not the 3.11 of running-test-workloads.md §2's example)* matches TypeWright's dev and app
image, so the generated tests run on the same interpreter they were parsed and generated against — no
version-skew false results. *Pinned to the exact lock versions, not floating,* because `results.py` parses bugs
by **text-scraping** pytest's `FAILED …` and Hypothesis's `Falsifying example: …` markers (D39); a future
release that reworded those would silently break the parser, so the sandbox must run the versions TypeWright was
built against. *Minimal (just pytest + hypothesis),* because every generated file imports only those two plus
stdlib the function-under-test uses — numpy/pandas were considered and deferred as speculative (no current input
needs them; easy to add a layer when one does). *Verifying how Kestrel launches the container first* (reading
its `docker_executor.py`) is what let us drop `USER`/`WORKDIR`/`CMD`: the network-less, read-only,
uid-65534, 256 MiB/1-CPU/64-pid sandbox is Kestrel's boundary, so the image is deliberately a plain interpreter
+ the two deps and nothing defensive of its own. (Live end-to-end smoke is the final Phase-5 step, run once
Kestrel is up; WSL2 does not enforce the 256 MiB cap, so a local smoke won't catch OOM.)

## Phase 6 — Fix Suggestions

### D44 — Opt-in fix suggestion is best-effort, not part of the all-or-nothing chain (Phase 6)
**Decision:** A new `src/typewright/fixgen.py` adds the fourth and final LLM call (`suggest_fix`, stage
`"fix_suggestion"`): given the function source + the `BugReport` from running the generated tests, the model
returns a `ProposedFix` (a corrected function as source + a one-line explanation). Two models, mirroring D35's
raw→artifact split: `ProposedFix` is the LLM's raw output; `FixSuggestion` is the verified artifact the API
returns (`code`, `verified`, `tests_passed`, `tests_failed`, plus the extra honest fields `explanation` and a
fixed `disclaimer` carrying the brief's "AI suggestion — review carefully" label — allowed by D5, which forbids
only promised-but-unreal fields; cf. D40). The step is **opt-in**: `AnalyzeRequest` gains
`include_fix_suggestion` (default **false** — the field D22 deferred to this phase), and the route runs the fix
step **only when the caller set it AND bugs were found** (no bugs → nothing to fix → no LLM call).
`AnalyzeResponse` gains `fix_suggestion: FixSuggestion | None` — an honest three-state: `null` = not requested
or no bugs; present with `verified=false` = the brief's "no confident fix"; present with `verified=true` =
proven. Crucially, the fix step is **best-effort and breaks the all-or-nothing rule** (D30/D36/D41): a
fix-generation `PipelineError` → `fix_suggestion: null`; an unrunnable corrected file or a verification
timeout/transport error → `verified=false` — none of these become a 500/504. Orchestration lives in a small
`_maybe_suggest_fix` helper in `main.py`; `suggest_fix` is injected through a fifth `get_suggest_fix` dependency
(the established test seam, D21/D30/D36/D41).
**Why:** Every earlier stage is mandatory and all-or-nothing because each is a prerequisite for the next and a
partial pipeline is a dishonest result (D30/D36/D41). Fix suggestion is different on both counts: it is the
*last* step, downstream of an already-complete, already-valid analysis (the real value — `bugs_found` — is in
hand), and the brief itself calls it "optional" (§3 Step 7). Failing the whole request because the optional,
most-expensive step hiccuped would *discard* the valid bugs the caller came for — the worst trade. Best-effort
degradation keeps `bugs_found` intact and reports the fix as simply absent or unverified. Opt-in (default false)
is the same honesty/cost discipline as D22: the step adds a fourth LLM call **plus a second sandbox run**
(verification, D45), so a caller pays for it only by asking — the web demo (Phase 8) will expose it as a
checkbox, which is exactly "let the user decide per request". `ProposedFix` vs `FixSuggestion` keeps the seam
clean (the model owns the proposed code; `fixgen`/the route own the verdict), just as D35 split `GeneratedTests`
from `GeneratedTestFile`.

### D45 — Verify a fix by re-running the SAME tests; a single attempt, no refine loop (Phase 6)
**Decision:** A proposed fix is verified by **swapping the corrected function into the SAME generated test file
and re-running it in Kestrel** — never by trusting the model or by writing fresh tests. `fixgen.build_fix_file`
does the swap deterministically: it `ast.parse`-validates the corrected source, confirms it defines a top-level
function of the original name, replaces the original function block (`testgen` embedded it as `meta.source.strip()`
verbatim, D32/D33, so an exact single-occurrence replace is reliable) and re-validates the whole reassembled
file — returning `None` if any of that fails. The route runs that swapped file through the **existing
`get_run_tests` seam** and the **pure `parse_results`** (so one mock covers both the initial and the
verification run), then `fixgen.finalize` reads the verdict: `verified` iff the re-run has no bugs, did not time
out, and exited 0. It is a **single attempt** — if the re-run still fails, the fix is surfaced with
`verified=false` ("no confident fix"); it is **not** iterated. A verification-run timeout or transport error
degrades to `verified=false` and does **not** 504/500 the request (unlike the *primary* run's timeout, D42).
**Why:** Re-using the *same* property tests as the oracle is the whole point: the tests are
implementation-independent relations (D23), so a green re-run is real evidence the bug is gone — whereas asking
the model to write new tests for its own fix, or to self-certify, is circular (the model grading its own work).
Swapping only the function definition (and owning that splice deterministically) means the imports and the
assertions are byte-for-byte the ones that *found* the bug, so nothing changed between "failing" and "passing"
except the code under test. `build_fix_file` returning `None` rather than raising keeps the best-effort contract
(D44): a malformed fix can't be verified, so it's reported unverified, not as a server error. Single-attempt
mirrors D19's "one structured call, defer the agentic draft→critique→refine loop until quality is shown to need
it": a refine loop multiplies cost (each round is another LLM call + another sandbox run) and code, and the MVP
exit metric (~60% verified on the golden set) is measurable without it. The verification timeout *not* mapping
to 504 follows from D44's best-effort rule — the primary `bugs_found` is valid and must survive a slow
fix-verification.

## Phase 7 — GitHub App

### D46 — Architecture: arq + Redis (Postgres deferred), one summary comment, best-effort per function
**Decision:** The GitHub App is a deterministic pipeline behind a webhook: `POST /webhook/github` verifies +
enqueues and returns fast; a **separate arq worker** (Redis-backed) does the minutes-long analysis and posts
**one summary issue comment** on the PR. Three forks, chosen for the leanest credible path to the exit criterion:
(1) **Queue = arq** (asyncio-native Redis queue) over Celery/RQ (sync, heavier ceremony) or FastAPI
BackgroundTasks (in-process, lost on restart) — it fits the async FastAPI/LiteLLM stack and is a real, durable
background queue. The blocking pipeline (LLM + sandbox, all sync) is offloaded to a **thread** inside the async
task (`asyncio.to_thread`) so it never blocks arq's event loop. (2) **Persistence = Redis only; Postgres
deferred** — the webhook delivery carries `installation_id`, so installation tokens are minted **on demand** per
job (no install table); run-history / a shareable `GET /v1/runs/{id}` link are not built yet. Postgres arrives in
Phase 8/9 when a feature first reads/writes it (D1). (3) **Output = one markdown issue comment** (PR comments are
issue comments) over Check Runs / inline annotations — the simplest GitHub surface that meets "see a comment
within 2 min"; richer formats are later polish. The worker is **best-effort per function**: one function's
pipeline failure is logged and skipped so a single bad function doesn't sink the whole PR, and the bot comments
**only when bugs are found** (clean PRs get no comment — avoids noise).
**Why:** Every earlier phase ran in-process behind one request; Phase 7 is the first that genuinely can't —
GitHub needs a sub-10s 2xx, and per-PR analysis is minutes of LLM + sandbox work across several functions, so the
work MUST be out-of-process and durable (a crash/redeploy mustn't drop a PR). arq is the smallest thing that
gives that and matches the async stack. Deferring Postgres keeps the phase to exactly the infra the exit
criterion needs (a queue) and honors D1 — installation tokens are short-lived and derivable from each delivery,
so persisting installs earns nothing yet. One comment over Check Runs keeps the GitHub API surface tiny (no
line-mapping, no check lifecycle). Best-effort-per-function mirrors the D44 fix-step lesson at PR scale: the
value is the bugs we *did* find; one unanalyzable function (or a flaky stage) must not blank the whole report.

### D47 — Webhook: verify the RAW body, act only on real PR events, enqueue behind a seam
**Decision:** `webhook.py` holds two pure functions — `verify_signature` (constant-time HMAC-SHA256 of the
**raw** request body against `X-Hub-Signature-256`) and `parse_pull_request_event` (a minimal `PullRequestJob`
only for actions in {opened, synchronize, reopened}; everything else → `None`). The route is thin I/O: read the
raw body, verify, parse, enqueue via an injected `get_enqueue` seam (a capturing fake in tests, the real arq
`enqueue` in prod), and reply **202 queued / 200 ignored / 403 bad-signature / 400 bad-JSON**. When no
`github_webhook_secret` is configured, verification is **skipped with a warning** (dev only) — the same
empty-secret-disables idiom Kestrel uses for its dev API key.
**Why:** GitHub signs the exact bytes it sent, so verification must hash the raw body, never a re-serialized
payload (key ordering/whitespace would differ) — hence raw-body-in-the-route, pure-verify-in-the-module. The
actionable-actions filter keeps us off the firehose of PR sub-events (labels, assignments) that don't change
code. 202 (not the brief's illustrative 200) is the textbook "accepted for async processing." The pure/seam
split makes the security-critical bit (signature) unit-testable with a known HMAC and the route key-/network-free.

### D48 — A thin GitHub client (no SDK) + pure diff→functions, mirroring the existing seams
**Decision:** `github.py` is a thin **sync** httpx client (mirroring `kestrel.py`/D37, not the PyGithub SDK):
build an App JWT (RS256 via PyJWT, signed with the App's private-key **file**), exchange it for an
installation token on demand, then `list_pr_files` (paginated), `get_file_content` (raw media type), and
`post_comment`. Any failure raises **`GitHubError`** — deliberately NOT request-scoped (the worker runs off the
queue, not behind the API), so it maps to no HTTP status; the worker logs/skips. `diff.py` is pure: parse the
unified-diff `patch` into the set of changed NEW-file line numbers, then intersect with each top-level function's
AST line span (reusing the existing `parser`) to get the changed `FunctionMetadata`s. `analysis.py`'s
`analyze_one` reuses the **exact pipeline functions** the HTTP route uses (not the route itself) and always
attempts a verified fix when bugs exist; `comment.py` renders one markdown body. The private key is a file PATH,
not an inlined multi-line PEM in an env var.
**Why:** We need three endpoints, not the whole PyGithub surface — the same calculus as D37 (don't take a heavy
dep for a thin slice), and reusing the `kestrel.py` sync-httpx + `_client()`-seam shape means the tests look
identical (MockTransport, no network). Sync throughout (with the worker offloading to a thread, D46) avoids a
sync/async pipeline rewrite. Splitting "which lines changed" (pure patch parsing) from "which functions changed"
(AST intersection) keeps both unit-testable on plain strings and reuses the parser we already trust. A
file-path private key sidesteps multi-line-PEM-in-env pain and matches how GitHub hands you the `.pem`.

## Phase 8 — Web Demo

### D49 — Web demo: one self-contained page served by the API itself; no separate frontend, no new storage
**Decision:** Phase 8's demo is a SINGLE static HTML document — inline CSS + vanilla JS, no build step,
no external assets — held as the `INDEX_HTML` constant in a new `web.py` and served at **`GET /`**
(`response_class=HTMLResponse`, `include_in_schema=False`). The page POSTs to the existing
`POST /v1/analyze` on the **same origin** (so there is no CORS to configure) with
`include_fix_suggestion: true`, then renders the detected properties, each bug's failing input +
severity, and the collapsible **verified** fix (carrying the "AI suggestion — review carefully"
disclaimer). It pre-fills a buggy `absolute` so the page finds a real bug on the first click, and
degrades every non-200 (400 / 422 / 429 / 500-with-stage / 504 / network) into a readable line rather
than a blank screen. **Shareable links (`GET /v1/runs/{id}`) + a storage backend are deferred**, and
**per-IP rate-limiting is deferred to Phase 9** (which already owns limits/hardening).
**Why:** the engine is already a clean HTTP API, so the demo only needs a face on it. A same-origin
static page reuses that API verbatim — no second service, no Node toolchain, no CORS, no repo split —
and ships inside the existing wheel/image because it is a Python module constant (no static-asset
packaging or path concern). Choosing this over standing up Next.js or Postgres honors the project's
"add infra only when a feature needs it" rule (D1/D12): the exit criterion ("a recruiter pastes a
function and sees bugs in ~60s") needs neither storage nor a separate frontend, so neither is added.
(`INDEX_HTML` is a *raw* triple-quoted literal so the page's JS escapes survive verbatim; the example
function's `"""` docstring is assembled in JS from single double-quote characters so that three
double-quotes never appear in the source and close the literal early.)

### D50 — Shareable links: best-effort persistence of each run as a JSON blob in SQLite, behind a swappable seam
**Decision:** A completed analysis is saved so it can be fetched later by `analysis_id` (the brief's
`GET /v1/runs/{id}`). New `store.py` defines a small `RunStore` protocol (`save(response)` /
`load(id) -> AnalyzeResponse | None`) with two implementations: `SqliteRunStore` (the real one — stdlib
`sqlite3`, a single-file DB, one short-lived connection per call so it is safe under FastAPI's threadpool,
WAL mode; one row per run = `analysis_id` PK + UTC `created_at` + the full `AnalyzeResponse` as a JSON
`body`) and `InMemoryRunStore` (a dict — the test/dev fake). `POST /v1/analyze` saves the response through
a new `get_run_store` dependency seam **best-effort** — a `store.save` failure is logged and swallowed,
never failing the already-valid analysis (the D44 lesson). `GET /v1/runs/{analysis_id}` returns the stored
response, or **404** (`HTTPException`) on an unknown/expired id. New config `runs_db_path` (default
`runs.db`; point at a mounted volume in a container so links survive a redeploy). No eviction yet —
`created_at` is recorded for a future TTL/cleanup pass.
**Why:** SQLite is the lightest thing that *durably* backs shareable links — stdlib (no new dependency),
no service, no compose change — so it honors the project's "add infra only when a feature needs it" rule
(D1/D12) better than standing up Postgres or reusing the worker's Redis (whose default is in-memory, so
links would die on redeploy). The `RunStore` protocol keeps the choice swappable: Postgres can drop in for
a Phase-9 multi-replica deploy with no route change. Storing the whole response as one JSON blob (over a
normalized schema) makes the read path a single row → `model_validate_json`, and the stored shape tracks
`AnalyzeResponse` automatically as it evolves. Persistence is best-effort for the same reason the fix step
is (D44): the analysis is the value; a storage hiccup must degrade the *link*, not the result. A lookup
miss is a plain 404 — neither a caller-input error (400) nor a pipeline failure (500) — so it needs no
domain-error type.

## Phase 9 — Observability, Cost Controls, Hardening

### D51 — Run metadata is real; LLM cost is metered at the chokepoint via a request-scoped contextvar
**Decision:** `AnalyzeResponse.metadata` (deferred by D5 to "the phase that makes it real") is now
populated: `analysis_duration_ms` (wall-clock around the pipeline), `llm_cost_usd` (summed LiteLLM cost of
the analysis's LLM calls), `tests_generated` (`len(test_file.test_names)`), `tests_run`
(`tests_passed + tests_failed` from the sandbox run), and `hypothesis_examples_tried` (`int | None`, left
**null** — not yet instrumented; an honest null rather than a fabricated `0`, per D5/D40). Cost is captured
as a **cross-cutting concern, not threaded through every step**: new `metrics.py` holds a `CostMeter` bound
to the request by a `contextvars` `cost_scope()` that `/v1/analyze` opens around the whole pipeline; the
single LLM chokepoint `llm.complete` switches to Instructor's `create_with_completion` (so it can see the raw
response) and calls `metrics.add_cost(raw)`, which adds that completion's LiteLLM-computed cost to the active
meter (a no-op outside a scope). A model missing from LiteLLM's price map, or any cost error, degrades to
`0.0` — cost is **reported, never enforced** here (the budget is Unit 2). Hand-written test fakes expose only
`create()`, so `complete` falls back to it (a `hasattr` guard) and the existing step tests are untouched.
**Why:** the metadata block is the spec's §5 contract and the foundation the cost budget (U2) needs. Metering
at the chokepoint with a request-scoped contextvar keeps every step's signature and tests unchanged — cost
accounting doesn't belong in the analysis logic, and threading an output sink through four unrelated steps
would be noise. `create_with_completion` yields the cost **synchronously in the same thread** — more robust
than a global LiteLLM success-callback, whose in-context firing under FastAPI's threadpool is harder to
guarantee. Best-effort costing (never throwing) keeps a price-map miss from sinking a valid analysis — the
same degrade-don't-fail rule as D44.

### D52 — Per-analysis cost budget: a hard ceiling enforced at the meter, surfaced as 402
**Decision:** An analysis aborts if its LLM cost crosses a ceiling. Config `max_cost_usd` (default 0.50) is
the server's hard cap; a request may set its own `max_cost_usd` but only to **lower** it
(`min(request, config)`, never above — the same clamp-down rule Kestrel uses for the timeout, D41). The
ceiling is enforced where the cost is already known — the `CostMeter`: it carries a `limit_usd`, and `add()`
raises `CostBudgetExceededError(spent, limit)` the instant the running total crosses it. The route opens
`cost_scope(effective_budget)`; because `add_cost` runs at the `llm.complete` chokepoint *after* each
completion, the call that crosses the line completes (already paid for) and the next never fires — **spend
is bounded at the ceiling plus at most one in-flight call.** `complete` lets the error propagate (not wrapped
into a 500); a new handler maps it to **402 Payment Required** with `spent_usd`/`limit_usd`. The best-effort
fix step (D44) catches it too and degrades to `fix_suggestion: null`, so a 402 only escapes the **core**
pipeline — once `bugs_found` is valid, an over-budget *fix* is dropped, not surfaced as 402.
**Why:** cost control belongs at the one place that already knows the cost (the meter at the LLM chokepoint),
so no step signature changes and the cap can't be bypassed. Clamping the request down to a server cap
protects the operator's bill on a public, unauthenticated demo (general rate limiting is U3; this is the
per-analysis guard). 402 is the precise HTTP semantic for "a spend limit was hit," kept distinct from 429
(rate, U3) and 400 (bad input). Degrading the fix step rather than 402-ing it honors D44: the analysis is
already valuable; only the optional fix is sacrificed. **Known limitation:** Instructor validation *retries*
make extra LLM calls but only the final response is billed (we read `create_with_completion`'s final raw),
so the meter slightly under-counts — conservative for the operator, acceptable.

### D53 — Rate limiting: a fixed-window limiter behind a seam; in-memory default, Redis-switchable
**Decision:** `POST /v1/analyze` is limited per client IP and `POST /webhook/github` per GitHub
`installation_id` — a fixed-window counter (`rate_limit_*_per_minute`, default 10/IP and 30/install),
returning **429** + a `Retry-After` header (new `RateLimitedError`). New `ratelimit.py` defines a
`RateLimiter` protocol with two implementations: `InMemoryRateLimiter` (per-process, zero-infra — the
**default**, correct for a single instance) and `RedisRateLimiter` (`INCR`+`EXPIRE`, shared across
replicas, **fails open** on a Redis error so a limiter blip can't take down the API — the U2 cost budget is
the hard backstop). `create_app` builds one from `rate_limit_backend` ("memory"|"redis") onto `app.state`;
a `get_rate_limiter(request)` dependency reads it (per-app-instance → no cross-test bleed, and overridable
in tests). The client IP is the peer address unless `trust_forwarded_for` is set, in which case the first
`X-Forwarded-For` IP is used (trustworthy only behind a proxy/tunnel you control — off by default so a
client can't spoof it). Cheap reads (`GET /`, `/v1/runs/{id}`, `/health`) are unlimited.
**Why:** rate limiting is what makes a public, unauthenticated demo safe to leave running, and is the
Phase-9 "production-ready under load" deliverable. The seam + two backends honors both "simplest that works
now" (in-memory needs no infra and fits the single-host deploy) and "best for the future" (the Redis
production path ships and flips on with one config value — no future code change). Per-IP for the web and
per-installation for the webhook match where the cost/abuse actually originates. 429 + Retry-After is the
standard contract; keeping it distinct from 402 (cost, D52) lets a caller tell "too fast" from "too
expensive." Fixed-window over sliding/token-bucket: simplest to reason about, one counter per key, adequate
for a demo. Trusting `X-Forwarded-For` only behind a configured proxy avoids the classic header-spoof bypass.

### D54 — Tracing: per-analysis structured traces in the logs (stage timeline + outcome), backend-agnostic
**Decision:** Each analysis emits ONE structured trace — the pipeline stage timeline plus the outcome — to
the logs. New `tracing.py` mirrors `metrics.cost_scope`: a request-scoped `Trace` bound by a contextvar; the
`/v1/analyze` route opens `trace_scope(analysis_id, …)` around the pipeline and wraps each stage in
`span(name)`, which times the block and records `(name, duration_ms)` on the active trace. On exit the trace
logs a summary line (a logfmt message plus the fields under a structured `extra`): `function`, `model_tier`,
per-stage `*_ms`, total `duration_ms`, `llm_cost_usd`/`llm_calls` (from the U1 meter), `bugs`,
`tests_generated`/`tests_run`, `fix_verified`. The `analysis_id` doubles as the trace id, correlating the
log, the response, and the shareable `?run=` link. `config.log_format` ("text" default | "json") switches
the formatter so the fields are machine-parseable for any aggregator. No external service; the emit is a
single log call, so Langfuse or OTel is a drop-in later without touching the route.
**Why:** for a single service, structured logs ARE full tracing — and they're the most *portable* choice: the
same line ingests into Loki/Datadog/CloudWatch/ELK or even Langfuse, with zero infra or vendor lock-in. That
is a stronger "best for the future" than wiring one tracing vendor (which couples us to a running service for
a feature only the operator views). It reuses what U1–U3 already built (the cost meter, the per-stage
boundaries, the `analysis_id`), so it is nearly free. The contextvar + `span` shape matches `cost_scope`, so
the mechanism is already familiar. Emitting on scope exit (even on error) means a failed run still leaves a
trace showing how far it got. JSON is opt-in so the dev console stays human-readable by default.

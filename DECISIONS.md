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
sits a layer above any single provider's SDK, so provider-specific features (e.g. Anthropic
prompt caching, adaptive-thinking controls) aren't first-class — revisit for the hot path if a
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

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

## Phase 2 — LLM Contract Inference

### D13 — Structured contract output via Instructor; API key passed explicitly
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

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

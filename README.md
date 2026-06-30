# TypeWright

**AI-powered property-based test generator for Python.**

TypeWright finds the bugs your test suite never thought to look for. For a Python
function, it detects which well-known *property classes* the function should satisfy
(round-trip, idempotence, metamorphic relations, invariants, postconditions, and more),
generates [Hypothesis](https://hypothesis.readthedocs.io/) property-based tests that
assert them, executes those tests in an isolated Docker sandbox
(Kestrel), and reports the exact inputs that break the
function — plus, on request, a corrected version of the function that it has **verified**
by re-running the same tests.

It ships in two form factors: a **GitHub App** that comments on pull requests, and a
**web demo** you can paste a function into. It is a deliberate productization of the
LLM-driven property-based testing technique in *Agentic Property-Based Testing*
(arXiv:2510.09907), not novel research — see [Acknowledgment](#acknowledgment).

> **Status:** Phases 1–9 complete (parsing, property detection, strategy + test
> generation, sandbox execution, fix suggestions, GitHub App, web demo, observability /
> cost controls / hardening). **Phase 10 — polish & launch — is in progress.** 170 tests.
> The maintained spec is [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md); design decisions and
> their rationale are in [`DECISIONS.md`](DECISIONS.md).

## How it works

A deterministic chain — each step is one specific LLM call with structured output, not a
free-form agent:

1. **Parse** (no LLM) — stdlib `ast` extracts the function's name, signature, type
    hints, docstring, and body.
2. **Detect properties** (LLM) — the model *recognizes* which property classes the
    function fits from its name/signature/types/docstring, with a confidence per property.
    It does not synthesize a spec from the body — that would be a circular oracle.
3. **Generate strategies** (LLM) — Hypothesis input strategies, validated with
    `ast.parse()` before use.
4. **Generate the test file** (LLM) — a complete pytest file asserting the properties.
5. **Execute in Kestrel** — runs the tests in an isolated container, capturing the exact
    counter-examples that break each property.
6. **Suggest a fix** (opt-in, LLM) — when bugs are found, propose a corrected function and
    **verify** it by re-running the same tests in the sandbox.

### Property classes

`round_trip` · `idempotence` · `invariant_preservation` · `metamorphic` ·
`type_postcondition` · `value_postcondition` · `totality`. A function may fit several;
the stronger value/postcondition classes catch *wrong answers*, while `totality` (the
weakest) only catches crashes. See `PROJECT_BRIEF.md` §3 for the full taxonomy.

## Architecture

Two independent services that compose, plus a client SDK:

| Service | Responsibility | Does **not** handle |
|---|---|---|
| **TypeWright** (this repo) | AI pipeline: parse, detect, generate, run, report. Front doors: GitHub webhook + web UI. | Code execution, container isolation. |
| **Kestrel** | Runs Python in an isolated, resource-limited container; returns structured results over REST. | AI/LLM calls, GitHub, UI. |
| **kestrel-client** | Python SDK for Kestrel (auth, retries, parsing). TypeWright drives Kestrel over HTTP directly and does not depend on it. | Anything AI or deployment. |

The boundary keeps AI concerns and sandbox-security concerns cleanly separated. See
[`running-test-workloads.md`](running-test-workloads.md) for how TypeWright drives
Kestrel.

## Quickstart

**Prerequisites:** [uv](https://docs.astral.sh/uv/), Python 3.11+, Docker (for the
Kestrel sandbox), and an Anthropic API key. Redis is only needed for the GitHub App path.

```sh
# 1. Install (creates the venv, installs runtime + dev deps)
uv sync

# 2. Provide your API key (either form works)
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Start Kestrel (the sandbox) — separate repo, needs Docker.
#    The 60s execute-timeout override is required for test workloads.
#    See running-test-workloads.md for the full recipe.
KESTREL_EXECUTOR_DOCKER_IMAGE=typewright-test-runtime:0.2 \
KESTREL_EXECUTE_TIMEOUT_SECONDS=60 \
uv run --directory /path/to/Kestrel \
uvicorn kestrel.app:create_app --factory --port 8000

# 4. Start TypeWright (Kestrel defaults to localhost:8000, so use another port)
uv run uvicorn typewright.main:create_app --factory --port 8001
```

**Web demo:** open <http://localhost:8001/> — paste a function (the buggy `absolute`
is pre-filled), click Analyze, get properties + bugs + a verified fix. The result is
shareable via the `?run=<id>` link.

**API:**

```sh
curl -s localhost:8001/v1/analyze \
-H 'content-type: application/json' \
-d '{
    "code": "def absolute(x):\n    if x < 0:\n        return x\n    return x\n",
    "function_name": "absolute",
    "include_fix_suggestion": true
}' | jq
```

**GitHub App:** in addition to the web process, run Redis and the background worker:

```sh
arq typewright.worker.WorkerSettings
```

Point your GitHub App's webhook at `POST /webhook/github` and set
`TYPEWRIGHT_GITHUB_WEBHOOK_SECRET`, `TYPEWRIGHT_GITHUB_APP_ID`, and
`TYPEWRIGHT_GITHUB_APP_PRIVATE_KEY_PATH`. See `PROJECT_BRIEF.md` for the full setup.

## HTTP endpoints

| Method & path | Purpose |
|---|---|
| `GET /` | The web demo (single self-contained page). |
| `POST /v1/analyze` | Analyze one function; returns properties, bugs, and an optional verified fix. |
| `GET /v1/runs/{id}` | Re-fetch a stored analysis by id (backs the share link). |
| `POST /webhook/github` | GitHub App webhook; enqueues a PR analysis. |
| `GET /health` | Liveness check. |

## Configuration

All settings load from the environment with the `TYPEWRIGHT_` prefix (or a `.env` file).
The most common:

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | LLM access (or `TYPEWRIGHT_ANTHROPIC_API_KEY`). |
| `TYPEWRIGHT_KESTREL_BASE_URL` | `http://localhost:8000` | Where Kestrel is reachable. |
| `TYPEWRIGHT_DEFAULT_MODEL_TIER` | `standard` | `economy` (Haiku 4.5), `standard` (Sonnet 4.6), `premium` (Opus 4.8). |
| `TYPEWRIGHT_MAX_COST_USD` | `0.50` | Per-analysis LLM cost ceiling; crossing it returns `402`. |
| `TYPEWRIGHT_MAX_MONTHLY_COST_USD` | `10.00` | Global monthly LLM-spend ceiling across all analyses; crossing it returns `503` until month rollover. |
| `TYPEWRIGHT_REDIS_URL` | `redis://localhost:6379` | Queue for the GitHub App worker. |
| `TYPEWRIGHT_RATE_LIMIT_BACKEND` | `memory` | `memory` or `redis`; per-IP / per-installation limits. |
| `TYPEWRIGHT_LOG_FORMAT` | `text` | `text` or `json` (structured per-analysis traces). |

See [`src/typewright/config.py`](src/typewright/config.py) for the complete list,
including GitHub App credentials and Kestrel timeouts.

## Development

```sh
uv sync          # create the venv and install deps (runtime + dev)
uv run pytest    # run the test suite (170 tests)
```

Plain-language, per-module walkthroughs live in the sibling
`TypeWright_Code_Walkthrough/` folder.

## Acknowledgment

TypeWright productizes the LLM-driven property-based testing technique published in
*Agentic Property-Based Testing* (arXiv:2510.09907, Oct 2025). It is a deliberate
productization in a new form factor (GitHub App + web demo), not novel research. Full
attribution is in [`ACKNOWLEDGMENTS.md`](ACKNOWLEDGMENTS.md).

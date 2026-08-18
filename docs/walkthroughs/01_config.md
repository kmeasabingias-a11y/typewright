# 01 — `src/typewright/config.py`

## What this file is for

This file is TypeWright's **settings panel**. It's the one place that holds all the
knobs the program might need to adjust — like the app's name, whether it's running on
your laptop or on a real server, and how chatty its logs should be.

Think of it like the settings screen on your phone. The phone has dozens of behaviors,
but you don't dig into the wiring to change them — you go to one settings screen. This
file is that screen for TypeWright. And crucially, the settings can be changed *from
outside the program* (through "environment variables", explained below) without editing
any code.

The panel started nearly empty in Phase 1 and grew in Phase 2, which added the AI model's
API key, a set of model "tiers", and a few tuning knobs for AI calls. Building the panel
early paid off: each new setting was just one more labelled field, not a new mechanism. (The
address of the Kestrel sandbox will join the same way in Phase 5.)

---

## A mental model: what is an "environment variable"?

An **environment variable** is a named value that lives *outside* your program, in the
operating system around it. Your program can read it when it starts.

Why bother, instead of just writing the value in the code? Two reasons:

1. **Secrets shouldn't live in code.** An AI model API key is a password. If you type it
   into a `.py` file and push that to GitHub, you've leaked it to the world.
   Environment variables let the secret live on the machine, never in the code.

2. **The same code runs in different places.** On your laptop you might want lots of logs
   and a fake database. On the real server you want fewer logs and the real database.
   Same code, different settings — environment variables make that possible without
   changing a single line.

The tool we use to read these values is a library called **pydantic-settings**. You
describe the settings you want as a normal Python class, and it automatically fills them
in from environment variables, checking the types as it goes.

---

## The whole file

```python
"""Application configuration, loaded from environment variables.

A single ``Settings`` object is the typed, validated source of truth for all
runtime configuration (D9). Phase 2 adds the LLM model-tier mapping and call
tuning (D13, D17). The provider key is read from ``ANTHROPIC_API_KEY`` (or
``TYPEWRIGHT_ANTHROPIC_API_KEY``) and passed explicitly to the LLM client.
"""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TYPEWRIGHT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "TypeWright"
    environment: str = "dev"
    log_level: str = "INFO"

    # --- LLM / property detection (Phase 2) ---
    # Read from ANTHROPIC_API_KEY (no TYPEWRIGHT_ prefix) so the standard
    # provider env var works, or TYPEWRIGHT_ANTHROPIC_API_KEY if you prefer the
    # project prefix. Passed explicitly to the LLM client (D13).
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ANTHROPIC_API_KEY", "TYPEWRIGHT_ANTHROPIC_API_KEY"
        ),
    )

    # model_tier -> LiteLLM model string. The ``anthropic/`` prefix tells
    # LiteLLM which provider to route to (D17). IDs from the current catalog.
    model_economy: str = "anthropic/claude-haiku-4-5"
    model_standard: str = "anthropic/claude-sonnet-4-6"
    model_premium: str = "anthropic/claude-opus-4-8"
    default_model_tier: str = "standard"

    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2      # Instructor reask attempts on invalid output
    llm_max_tokens: int = 1024    # detected-property lists are small
    llm_temperature: float = 0.0  # deterministic detection; low temp curbs fabrication

    # --- Kestrel sandbox (Phase 5) ---
    kestrel_base_url: str = "http://localhost:8000"
    kestrel_api_key: str | None = None
    kestrel_timeout_seconds: float = 30.0
    kestrel_http_timeout_buffer_seconds: float = 15.0

    def model_for_tier(self, tier: str) -> str:
        """Resolve a request's ``model_tier`` to a concrete model string.

        An unknown tier falls back to the standard model, so a bad value
        degrades to a sensible default rather than erroring.
        """
        return {
            "economy": self.model_economy,
            "standard": self.model_standard,
            "premium": self.model_premium,
        }.get(tier, self.model_standard)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings singleton."""
    return Settings()
```

---

## Step-by-step

### The imports

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
```

- **`lru_cache`** — a small helper from Python's standard library. It remembers the
  result of a function so the work only happens once. We'll use it to make sure the
  settings are read a single time, not over and over.
- **`BaseSettings`** — the star of the show, from pydantic-settings. Any class that
  inherits from it automatically pulls its values from environment variables.
- **`SettingsConfigDict`** — a small object for configuring *how* `BaseSettings`
  behaves (where to look, what prefix to use, etc.).

### The settings class

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TYPEWRIGHT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
```

`class Settings(BaseSettings)` means "make a settings class with all the magic
pulling-from-the-environment behavior built in."

The `model_config` block tunes that behavior:

- **`env_prefix="TYPEWRIGHT_"`** — only environment variables that start with
  `TYPEWRIGHT_` are considered ours. So the field `log_level` is set by the environment
  variable `TYPEWRIGHT_LOG_LEVEL`. The prefix keeps our settings from clashing with
  unrelated things on the computer.

- **`env_file=".env"`** — during local development, you can put settings in a plain text
  file named `.env` instead of typing them into your terminal every time. (That file is
  ignored by git, so secrets in it never get committed.)

- **`env_file_encoding="utf-8"`** — just says the `.env` file is normal modern text.

- **`extra="ignore"`** — if the environment contains some `TYPEWRIGHT_SOMETHING` we
  didn't define, don't crash — just ignore it. Without this, an unexpected variable
  could stop the whole app from starting.

### The actual settings

```python
    app_name: str = "TypeWright"
    environment: str = "dev"
    log_level: str = "INFO"
```

These three lines are the settings themselves. Each has:

- a **name** (`app_name`),
- a **type** (`str`, meaning text),
- a **default value** (`"TypeWright"`), used when no environment variable is provided.

So out of the box, the app is called "TypeWright", thinks it's in "dev" (development)
mode, and logs at the "INFO" level. Any of these can be overridden — e.g. setting the
environment variable `TYPEWRIGHT_LOG_LEVEL=DEBUG` makes the app more talkative, with no
code change.

### The LLM settings (Phase 2)

These arrived in Phase 2, when TypeWright started asking an AI model to recognise which
property classes a function satisfies (see unit 09). They're all just more fields on the same
settings panel.

```python
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ANTHROPIC_API_KEY", "TYPEWRIGHT_ANTHROPIC_API_KEY"
        ),
    )
```

- **`anthropic_api_key`** is the password for the AI model. Its default is `None` (empty) on
  purpose — a real key must *never* be written into the code (see "What could go wrong").
- The **`validation_alias`** is the one twist. Normally every setting needs the
  `TYPEWRIGHT_` prefix, so this field would look for `TYPEWRIGHT_ANTHROPIC_API_KEY`. But the
  AI provider's *own* standard variable is plain `ANTHROPIC_API_KEY`. `AliasChoices` says
  "accept **either** name", so whichever one is already set on the machine works (D13).

```python
    model_economy: str = "anthropic/claude-haiku-4-5"
    model_standard: str = "anthropic/claude-sonnet-4-6"
    model_premium: str = "anthropic/claude-opus-4-8"
    default_model_tier: str = "standard"
```

- These map three **tiers** — `economy`, `standard`, `premium` — to three actual AI models,
  cheapest to most capable. A caller picks a tier (a short word) and never has to know the
  exact model name; the model behind each tier can change *here* without touching anything
  else.
- The **`anthropic/` prefix** is how the LLM library (LiteLLM) knows which provider to send
  the request to (D17).
- **`default_model_tier`** is which tier to use when the caller doesn't choose — `standard`.

```python
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2      # Instructor reask attempts on invalid output
    llm_max_tokens: int = 1024    # detected-property lists are small
    llm_temperature: float = 0.0  # deterministic detection; low temp curbs fabrication
```

- **`llm_timeout_seconds`** — give up on a slow AI call after 30 seconds rather than hanging.
- **`llm_max_retries`** — if the model returns something that doesn't fit the expected shape,
  ask it again, up to twice (the "Instructor" library handles this "re-ask").
- **`llm_max_tokens`** — a ceiling on the answer's length. The detected-property list is
  short, so 1024 is plenty and keeps each call cheap.
- **`llm_temperature`** — the "randomness" dial, set to `0.0` (the least random). Property
  detection should give the *same* answer for the same function every time, and a low
  temperature also discourages the model from inventing a property just to look useful.

The first three knob values are recorded as decision **D16**; the temperature is decision
**D25** (deterministic, anti-fabrication detection).

### The `model_for_tier` helper

```python
    def model_for_tier(self, tier: str) -> str:
        return {
            "economy": self.model_economy,
            "standard": self.model_standard,
            "premium": self.model_premium,
        }.get(tier, self.model_standard)
```

This little method translates a tier word into the real model string. The clever bit is the
`.get(tier, self.model_standard)` at the end: if someone passes a tier we don't recognise
(a typo, or a value from an old client), it doesn't crash — it quietly falls back to the
`standard` model. A bad input degrades to a sensible default instead of an error.

### The `get_settings` helper

```python
@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings singleton."""
    return Settings()
```

This little function builds and returns the settings. The important part is the
`@lru_cache` line just above it.

`@lru_cache` means "the first time this is called, do the work and remember the answer;
every time after, hand back the remembered answer." So no matter how many parts of the
program ask for the settings, they all get the **exact same one object**, and the
environment is only read once. (The fancy word for "one shared instance" is a
*singleton*.)

This is the standard, recommended way to share settings in a FastAPI app — and later it
also makes testing easy, because tests can swap in fake settings in one place.

---

## What could go wrong

### 1. Reading the environment many times
If we called `Settings()` directly all over the codebase, each call would re-read the
environment and build a new object. Wasteful, and it opens the door to different parts
of the app disagreeing about a setting. The `@lru_cache` on `get_settings()` prevents
this — everyone shares one object.

### 2. Forgetting the prefix
Because of `env_prefix="TYPEWRIGHT_"`, the variable that sets `log_level` is
`TYPEWRIGHT_LOG_LEVEL`, **not** plain `LOG_LEVEL`. Setting `LOG_LEVEL` and wondering why
nothing changed is an easy trap.

### 3. A stray variable crashing startup
Without `extra="ignore"`, any leftover `TYPEWRIGHT_*` environment variable on the machine
that doesn't match a defined field would make the app refuse to start. `extra="ignore"`
keeps it forgiving.

### 4. Putting secrets in the defaults
The non-secret defaults here are harmless (a name, a mode, a log level). The Phase 2
`anthropic_api_key` is different: its default is empty (`None`) on purpose — the real value
must come from the environment, never from the code. A real key written as a default would
get committed to git and leaked to anyone who can see the repo.

---

## Summary

`config.py` is the single, tidy settings panel for TypeWright. It uses pydantic-settings
so that values come from environment variables (safe for secrets, flexible across
machines), with sensible defaults for local development. `get_settings()` hands out one
shared, cached settings object to the rest of the program.

Phase 1 kept it nearly empty on purpose — the value was in having the *pattern* ready.
Phase 2 cashed that in: the LLM API key, the three model tiers, and the call-tuning knobs all
slotted in as plain new fields, with no new mechanism required. Kestrel's address will arrive
the same way in Phase 5.

---

## Change history

- **2026-06-09** — Created in Phase 1, Unit 2. Three settings: `app_name`,
  `environment`, `log_level`. No secrets yet.
- **2026-06-12** — Phase 2 groundwork: added the LLM settings — `anthropic_api_key` (read
  from `ANTHROPIC_API_KEY` or `TYPEWRIGHT_ANTHROPIC_API_KEY` via `AliasChoices`, D13); the
  `economy`/`standard`/`premium` model-tier strings + `default_model_tier` with the
  `model_for_tier()` resolver (LiteLLM `anthropic/` IDs, D17); and the call-tuning knobs
  `llm_timeout_seconds` / `llm_max_retries` / `llm_max_tokens` (D16).
- **2026-06-13** — Phase 2 redirect (D23): added `llm_temperature = 0.0` for deterministic,
  anti-fabrication property detection (D25), and re-labelled the LLM block "property
  detection" (was "contract inference"). Tuning-comment wording updated (`# detected-property
  lists are small`); no other config behavior changed.
- **2026-06-19** — Phase 5, Unit 1: added the Kestrel sandbox settings — `kestrel_base_url`
  (where `kestrel.py` reaches the sandbox; defaults to `http://localhost:8000`),
  `kestrel_api_key` (the `Bearer` key, `None` so it's omitted when Kestrel runs auth-off
  locally — both read via the `TYPEWRIGHT_` prefix), and `kestrel_http_timeout_buffer_seconds`
  (the margin added to the run budget for the HTTP read timeout, so the call outlives a long
  sandbox run; D37). No other config behavior changed.
- **2026-06-19** — Phase 5, Unit 3: added `kestrel_timeout_seconds = 30.0` — the default per-run
  sandbox budget the `/v1/analyze` route uses when a request omits `max_test_runtime_seconds`
  (D41). Kestrel additionally clamps any value down to its own server ceiling.
- **2026-06-25** — Phase 7: added GitHub App + queue settings — `github_webhook_secret` (HMAC secret for
  webhook verification; empty disables the check, a dev-only idiom), `github_app_id` +
  `github_app_private_key_path` (App identity for minting installation tokens; the key is a file path,
  not an inlined multi-line PEM), and `redis_url` (the arq job queue). D46/D47/D48.
- **2026-06-28** — Phase 8 (Unit 2a): added `runs_db_path` (default `runs.db`) — the SQLite file backing
  `GET /v1/runs/{id}` shareable links. A single-file DB needs no service; in a container point it at a
  mounted volume so links survive a redeploy. D50.
- **2026-06-28** — Phase 9 (Unit 2): added `max_cost_usd` (default `0.50`) — the server's hard per-analysis
  LLM-cost ceiling. A request's `max_cost_usd` can only lower it (`min(request, config)`); crossing it
  aborts with 402. D52.
- **2026-06-28** — Phase 9 (Unit 3): added rate-limit settings — `rate_limit_enabled` (default True),
  `rate_limit_backend` ("memory" default | "redis"), `rate_limit_analyze_per_minute` (10, per IP),
  `rate_limit_webhook_per_minute` (30, per installation), and `trust_forwarded_for` (default False — only
  trust `X-Forwarded-For` behind a proxy you control). D53.
- **2026-06-28** — Phase 9 (Unit 4): added `log_format` (default `"text"` | `"json"`) — switches the log
  formatter so the per-analysis trace summaries (`tracing.py`) are machine-parseable for an aggregator. D54.
- **2026-06-28** — D56: per-stage LLM token budgets. Bumped `llm_max_tokens` 1024 → **2048** (detection /
  strategy) and added `llm_max_tokens_codegen` (**4096**) for the code-emitting stages (testgen / fixgen).
  Found by real-repo testing — `inflection.underscore` truncated test generation at 1024 → 500. A cap is a
  ceiling not a cost, so raising it is free insurance against truncation.
- **2026-06-30** — Phase 10 (D58): added `max_monthly_cost_usd` (default **10.00**) — a global monthly LLM-spend
  ceiling across *all* analyses and entry points (web + worker + fix), enforced by `MonthlyCostMeter` and
  persisted in the `runs_db_path` SQLite file; crossing it returns **503** + `Retry-After` until the month rolls
  over. Set to 0 (or negative) to disable. Both the web and worker processes must share the same `runs_db_path`
  for one counter.
- **2026-06-30** — Phase 10 (D60): added `bug_verification_enabled` (default **True**) — turns on the
  second-opinion bug-verification step (`verify.py`, walkthrough 30) that attaches a `BugVerdict` to each
  reported bug. A per-request `verify_findings` overrides it. Best-effort and post-detection, so disabling it
  only removes the verdicts, never changes which bugs are found.
- **2026-08-16** — Phase 10 launch settings (D62/D63/D65). **New:** `max_daily_cost_usd` (default **2.00**,
  ≤ 0 disables) — the daily companion to the monthly cap; and `demo_access_code` (default `None` = open) —
  when set, `POST /v1/analyze` requires it via the `X-Demo-Access-Code` header or `?code=`, else 403.
  **Changed:** `model_standard` → `anthropic/claude-sonnet-5` and `model_premium` → `anthropic/claude-opus-5`
  (D63 — the tier indirection from D17 exists precisely so this is a config edit); and `llm_temperature` is
  now `float | None` defaulting to **None**, meaning *omit the parameter* (D65) — the current Claude models
  reject it. A pinned older model can still set a float. Note for any future tier change: verify LiteLLM can
  price the new model ID, because `metrics._response_cost` degrades to `0.0` on a price-map miss, which would
  silently disable every cost cap.

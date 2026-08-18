# 10 — `src/typewright/llm.py`

## What this file is for

This file is TypeWright's **one wall socket for talking to the AI**.

By Phase 3, *two* different parts of the program need to call a language model: Phase 2's
property **detection** (`inference.py`, unit 09) and Phase 3's strategy **generation**
(`generation.py`, unit 11). Both need the exact same thing first — an AI client that's been
wrapped so its replies come back as strict, checked shapes. Rather than each module building
that client itself (the same line copy-pasted in two places), we put the wiring in **one tiny
shared file**, and both modules plug into it.

Think of it like the single power outlet a workshop wires into the wall: the bench grinder and
the drill press both plug into it, and if you ever rewire the building, you change the *one*
outlet, not every tool. `llm.py` is that outlet — the single place that knows how our AI client
is built.

It exposes two functions: `build_client()`, which returns a ready-to-use, Instructor-wrapped
LiteLLM client; and `complete(...)`, which runs **one** structured AI call the exact same way every
analysis step does — the shared call recipe, added in Phase 4 once a third caller proved the
pattern.

---

## A mental model: why a whole file for three lines?

This is the smallest file in the project, so the interesting question isn't *what* it does —
it's *why it exists at all*. Two ideas:

**1. "Don't repeat yourself," but only once you actually repeat.** Back in Phase 2 there was
only one AI caller, so the client-building line lived right inside `inference.py`. We
deliberately did **not** split it out early — a shared module with a single user is just
indirection for its own sake. The rule we follow (recorded as decision **D18**) was: make the
split *when the second caller appears*. Phase 3 is that moment — `generation.py` is the second
caller — so now the shared file earns its place. (Decision **D27** is the actual "do it now"
call.)

**2. One place to change the AI wiring.** Everything provider-specific about *how* we build the
client lives here. If we ever switch the wrapper library, change how the client is constructed,
or add a setting that affects every call, we edit this one file and both callers come along for
free. The callers stay blissfully unaware of the plumbing — they just ask for a client.

A small but deliberate detail: each caller (`inference.py`, `generation.py`) still keeps its own
little `_client()` function that *calls* `build_client()`. Why not have them import
`build_client` and use it directly? Because each module's `_client()` is the **seam the tests
grab** — a test swaps out `inference._client` (or `generation._client`) for a fake so it never
touches the network. Keeping those per-module seams means this refactor changed nothing about
how the existing tests work (D18's promise: "the split can happen without disturbing this unit").
So `build_client` centralises the *real* construction, while each module keeps its *own* door
for tests to swap.

---

## The whole file

```python
"""Shared LLM plumbing: the Instructor-wrapped LiteLLM client every analysis step uses,
plus the one structured-completion call shape they all repeat.

Lifted out of ``inference.py`` once a second LLM caller (Phase 3 strategy generation)
appeared — the split DECISIONS.md D18 deferred to "the Phase 3 trigger" (D27). With a
*third* caller (Phase 4 test generation, D31) the call shape itself — the ``create(...)``
kwargs plus the ``PipelineError`` wrapping each stage repeated verbatim — is hoisted here
too, into ``complete()``. ``inference.py``, ``generation.py``, and ``testgen.py`` each keep a
thin ``_client()`` so tests can still stub the client per-module; they hand that factory to
``complete()``, which preserves the seam (D31).
"""

from __future__ import annotations

from typing import Callable, TypeVar

import instructor
from litellm import completion
from pydantic import BaseModel

from .config import Settings
from .errors import PipelineError

T = TypeVar("T", bound=BaseModel)


def build_client() -> instructor.Instructor:
    """Build the Instructor-wrapped LiteLLM client (D13/D17)."""
    return instructor.from_litellm(completion)


def complete(
    client_factory: Callable[[], instructor.Instructor],
    *,
    stage: str,
    settings: Settings,
    model: str,
    response_model: type[T],
    messages: list[dict[str, str]],
) -> T:
    """Run one structured Instructor completion, the way every analysis stage does (D31)."""
    if not settings.anthropic_api_key:
        raise PipelineError(stage, "no LLM API key configured")
    try:
        return client_factory().chat.completions.create(
            model=model,
            response_model=response_model,
            api_key=settings.anthropic_api_key,
            max_retries=settings.llm_max_retries,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
            messages=messages,
        )
    except PipelineError:
        raise
    except Exception as exc:  # noqa: BLE001 — any LLM/transport failure becomes a 500
        raise PipelineError(stage, str(exc)) from exc
```

*(The docstring on `complete` is trimmed here for the walkthrough; the real file carries the longer
note about preserving the test seam and reading the call-tuning values from `settings`.)*

---

## Step-by-step

### The imports

```python
import instructor
from litellm import completion
```

Just the two AI libraries: `instructor` (the layer that forces model replies into a strict
shape) and `completion` (LiteLLM's "make one AI call" function). These are the exact two that
*used* to be imported inside `inference.py`; the refactor moved them here, to their new single
home. Notice no provider brand is named; that lives only in the model-routing strings in
`config.py`, so this file stays provider-agnostic too.

### `build_client()`

```python
def build_client() -> instructor.Instructor:
    return instructor.from_litellm(completion)
```

The one line that matters: `instructor.from_litellm(completion)` takes LiteLLM's `completion`
function and wraps it, returning a smarter client (`instructor.Instructor`) that knows how to
coerce a reply into a Pydantic shape we ask for and re-ask the model when it doesn't fit. This
is the same wiring explained in detail in unit 09 — it has simply moved into its own function so
all three AI callers share it.

### `complete(...)`

Added in Phase 4 (decision **D31**). By then *three* steps — detection, strategy generation, and
test generation — were all making the same shape of AI call: the same `create(...)` with the same
keyword arguments, the same "no key? stop" check, and the same "wrap any failure as a
`PipelineError` naming this stage" handling. Three copies of that is the moment to extract it (the
"don't copy-paste the third time" rule from the mental model). `complete` is that one shared recipe.

Two details make it click:

- **It takes `client_factory` — a function, passed but not called.** Each module hands in its own
  `_client` (the bare name, no parentheses). `complete` calls the key-check *first*, and only then
  calls `client_factory()` to build the client. Because the caller hands in *its* `_client`, a test
  that swaps out `inference._client` for a fake is still in control — the per-module test seam
  survives the move untouched.
- **The call-tuning values come from `settings`.** `max_retries`, `max_tokens`, `temperature`, and
  `timeout` were identical across all three callers, so `complete` reads them straight from
  `settings` rather than making each caller pass them.

The `stage` argument is what lets one shared function still produce caller-specific errors: detection
passes `"property_detection"`, generation passes `"strategy_generation"`, test generation passes
`"test_generation"`, and the resulting `PipelineError` names whichever one failed.

So the file now owns two identical-for-everyone things: how the client is built (`build_client`) and
how a single structured call is made and its failures handled (`complete`). What still stays with
each caller is what genuinely *differs* per step: the settings object they pass, their prompt, and
the response shape they ask for.

---

## What could go wrong

### 1. Splitting too early (the mistake we avoided)
Had we created `llm.py` back in Phase 2, it would have been a file with a single user — pure
indirection, more files to read for no benefit. Waiting until a real second caller existed (D18)
means the shared module is justified the moment it's born. The lesson cuts both ways: don't
copy-paste the *third* time either — that's exactly when to extract.

### 2. Pulling too much into the shared file — too early
It was tempting back in Phase 3 to move the *call* itself here too — the `create(...)` with all its
keyword arguments and the `PipelineError` wrapping. We deliberately **left it in each caller then**
(D27): with only two callers, a shared helper used in two slightly-different ways can be more
confusing than two honest copies. The rule was "extract on repeat, not on prophecy" — wait for the
*third* caller to prove the shape. Phase 4's test generation is that third caller, so the call shape
*has now been hoisted up* into `complete()` (D31). The lesson holds both ways: we didn't split early,
and we didn't keep copying once the pattern was proven.

### 3. Breaking the test seam
If the callers had been changed to import and use `build_client` directly (dropping their own
`_client()`), every existing inference test that does `monkeypatch.setattr(inference, "_client",
...)` would have broken. Keeping each module's thin `_client()` wrapper preserved all those
seams, so the refactor stayed invisible to the test suite.

---

## Summary

`llm.py` is the single shared outlet for TypeWright's AI calls. It holds two functions:
`build_client()`, returning the Instructor-wrapped LiteLLM client; and `complete(...)`, running one
structured call the same way every step does — key-check, the `create(...)` with its tuning kwargs,
and `PipelineError` wrapping that names the failing stage. Property detection (unit 09), strategy
generation (unit 11), and test generation (unit 12) all rely on it. It grew in two steps: Phase 3
extracted `build_client` once a *second* AI caller appeared (the trigger D18 named, enacted by D27);
Phase 4 hoisted `complete` once a *third* caller proved the call shape (D31). Each caller still keeps
its own thin `_client()` and hands it to `complete`, so the existing test seams are undisturbed. Only
the parts identical for every caller live here; settings, prompts, and the requested response shape
stay with the callers that differ.

---

## Change history

- **2026-06-14** — Created in Phase 3, Unit 1. Holds `build_client()` (the Instructor + LiteLLM
  client construction), lifted out of `inference.py` now that strategy generation is a second LLM
  caller (D27, resolving the split D18 deferred to "the Phase 3 trigger"). `inference.py` and
  `generation.py` each keep a thin `_client()` delegating here, preserving their per-module test
  stubs. Suite green at 34 passed.
- **2026-06-15** — Phase 4, Unit 1. Added `complete(client_factory, *, stage, settings, model,
  response_model, messages)` — the shared structured-call recipe (key-check → `create(...)` with the
  settings-driven tuning kwargs → `PipelineError(stage, …)` wrapping), hoisted now that test
  generation (`testgen.py`) is the *third* caller (D31). It takes each module's `_client` as a
  factory (passed, not called), so all three modules' monkeypatch seams stay intact and the Phase 2/3
  suites were unchanged. `inference.py` and `generation.py` refactored to call it. Suite green at 45
  passed.
- **2026-06-28** — Phase 9 (Unit 1, D51): `complete` now bills LLM cost. When the (real Instructor) client
  exposes `create_with_completion`, it calls that instead of `create` to get the raw response alongside the
  parsed model, then `metrics.add_cost(raw)` charges the active request's cost meter (a no-op outside a
  `cost_scope`). Hand-written test fakes only have `create()`, so a `hasattr` guard falls back to it — the
  existing step tests are untouched. See `26_metrics.md`.
- **2026-06-28** — Phase 9 (Unit 2, D52): `complete` now lets `CostBudgetExceededError` propagate alongside
  `PipelineError` (the `except (PipelineError, CostBudgetExceededError): raise` line) — so a budget abort
  raised by `add_cost` reaches the route's 402 handler instead of being wrapped into a 500.
- **2026-06-28** — D56: `complete` gained an optional `max_tokens` override (falls back to
  `settings.llm_max_tokens`). The code-emitting stages (`testgen`, `fixgen`) pass the larger
  `settings.llm_max_tokens_codegen`; detection/strategy keep the default. Fixes a real truncation-→-500 on
  property-rich functions found by testing on `inflection`.
- **2026-06-30** — Phase 10 (D58): `complete` now also enforces the **global monthly cost cap**. A new
  `_monthly_meter(settings)` helper builds a `MonthlyCostMeter` (or `None` when the cap is disabled); on the
  real-client path only, `monthly.check()` runs **before** the call (raising `MonthlyBudgetExceededError` → 503
  once the month is used up) and `monthly.add_from_raw(raw)` records its cost after `add_cost`.
  `MonthlyBudgetExceededError` joins the `except (...): raise` tuple so it propagates instead of being wrapped
  into a 500. Driving it from `settings` (not a global) means the worker participates by sharing `runs_db_path`,
  and the fake-client step tests are untouched. See `26_metrics.md`.
- **2026-08-16** — Phase 10 (D62/D65), two changes to the request the chokepoint builds.
  (1) `_monthly_meter()` became **`_budget_meters(settings)`**, returning every global ceiling in force
  (monthly D58 + daily D62, each skipped when its cap is ≤ 0); `complete()` pre-checks them all before the
  call and bills them all after it, so whichever ceiling is reached first stops further spend.
  (2) **`temperature` is no longer sent unless configured.** The current Claude generation (Sonnet 5 /
  Opus 5 and the 4.7+ family) *removed* sampling parameters, so passing `temperature=0.0` returns
  `400 "temperature is deprecated for this model"` — this took down the whole detection stage in the
  pre-launch smoke while all 197 unit tests passed, because the hand-written client fakes accept any kwargs.
  `settings.llm_temperature` is now `float | None` defaulting to `None`, and the kwarg is added only when it
  is set. This deliberately is **not** delegated to LiteLLM: `get_supported_openai_params()` still claims
  these models support `temperature` (checked on 1.88.1), so a capability lookup would have kept sending it.
  See `01_config.md` and `26_metrics.md`.

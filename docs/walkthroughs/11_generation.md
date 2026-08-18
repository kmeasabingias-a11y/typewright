# 11 — `src/typewright/generation.py`

## What this file is for

This file is where TypeWright **asks the AI how to manufacture test inputs** for a function.

Phase 2 (unit 09) figured out *what rules* a function should obey — the property classes it
satisfies, like "running it twice is the same as once." But a rule is useless without inputs to
test it against. To check that `slugify(slugify(s)) == slugify(s)`, you need a generous supply of
example strings `s` — short ones, empty ones, ones full of punctuation and emoji. This file
produces the **recipe** for generating those examples: a **Hypothesis strategy** for each
argument.

Think of it like stocking a test kitchen before the taste-test. Phase 2 wrote the rule ("the
dish should taste the same however you stir it"); Phase 3 stocks the pantry with the *right
ingredients to try* — for a string argument, "any text"; for a price, "any non-negative number";
for a parser, "well-formed input." Phase 4 will later run the actual taste-test (write the test
file that feeds those ingredients in and checks the rule). This file just fills the pantry.

It exposes one function: `generate_strategies(meta, analysis)` takes the parsed function plus its
Phase 2 `PropertyAnalysis` and returns a `StrategyPlan` — one strategy per argument, plus any
extra imports those strategies need.

---

## A mental model: a few ideas that make this file obvious

**1. A Hypothesis "strategy" is a recipe for random values.** **Hypothesis** is the
property-based-testing library TypeWright is built around. Instead of testing one hand-picked
example, you give Hypothesis a *strategy* — `st.integers()` means "any integer", `st.text()`
means "any string" — and it generates dozens of varied values, deliberately including nasty edge
cases (0, empty string, huge numbers), hunting for one that breaks your rule. A strategy is just
a Python expression; you hand it to a test with `@given(arg=<strategy>)`. This file's whole job
is to produce, per argument, the right such expression as a string.

**2. This is the *second* AI call, and it looks just like the first.** If you've read unit 09,
this file will feel familiar on purpose — same skeleton: a system prompt (the standing job
description), few-shot examples, one structured Instructor call at temperature 0, and every
failure funnelled into a `PipelineError`. The difference is only the *question* asked and the
*shape* returned. Building it to mirror `inference.py` keeps the two AI steps easy to read side
by side.

**3. The shared client lives next door now.** The actual AI-client construction moved into
`llm.py` (unit 10) when this file became the second caller. So `generation.py` doesn't build the
client itself — it calls `build_client()` through its own thin `_client()` seam (kept local so
tests can still swap it). See unit 10 for why.

**4. "Valid inputs" is the whole game — and over-constraining is the trap.** The strategies must
generate values *in the function's intended domain* (you can't fairly test `parse_price` on
random gibberish), but they must not be *narrower* than that domain. An over-tight strategy is
worse than a loose one: if it never generates the input that triggers a bug, the bug hides and
the test passes green while being useless. So the prompt's strongest instruction is "reason from
the type first, tighten only when clearly justified, and when unsure stay broad with a lower
confidence."

**5. If our AI step breaks, that's *our* fault — a 500.** Exactly as in unit 09: the caller's
function was fine, so any failure here becomes a `PipelineError` for the `"strategy_generation"`
stage (decision **D15**), which the web layer turns into a 500. (Strategy generation isn't wired
into the API *yet* — that's a later unit, **D28** — but the error machinery is already correct
for when it is.)

---

## The whole file

```python
"""Phase 3: generate Hypothesis input strategies from detected properties + types.

``generate_strategies`` takes the parser's ``FunctionMetadata`` and the Phase 2
``PropertyAnalysis`` and returns a ``StrategyPlan``: one Hypothesis strategy per argument
(plus any extra imports) that a later phase drops straight into ``@given(...)`` (D24/D29).

Mirrors ``inference.py``: one structured Instructor call (D19) through the shared client
(D27), at low temperature with few-shot (D25). Any failure becomes a ``PipelineError``
(stage "strategy_generation", D15) -> HTTP 500.
"""

from __future__ import annotations

import instructor

from .config import Settings, get_settings
from .errors import PipelineError
from .llm import build_client
from .models import FunctionMetadata, PropertyAnalysis, StrategyPlan

_STAGE = "strategy_generation"

_SYSTEM_PROMPT = (
    "You are a property-based-testing assistant that writes Hypothesis input strategies. "
    "You are given ONE Python function (signature, argument types, docstring) and the "
    "property classes it has been found to satisfy. Produce ONE strategy per argument that "
    "generates VALID inputs — values in the function's intended domain.\n\n"
    "Reason from the declared TYPE first, then tighten ONLY when the name, docstring, or a "
    "detected property clearly implies a narrower domain (a parser needs well-formed text; a "
    "non-negative quantity uses min_value=0). Do NOT over-constrain: when unsure, use the broad "
    "strategy for the type and a lower confidence — an over-narrow strategy hides bugs by never "
    "generating the inputs that trigger them.\n\n"
    "Each strategy must be a Python expression usable directly in @given(arg=<strategy>), using "
    "`st` (from `from hypothesis import strategies as st`). Common mappings: int -> st.integers(); "
    "float -> st.floats(allow_nan=False); str -> st.text(); bytes -> st.binary(); bool -> "
    "st.booleans(); list[int] -> st.lists(st.integers()). If a strategy needs another import "
    "(e.g. base64), list it in extra_imports.\n\n"
    "For each argument give: argument (its name); strategy (the expression); a short rationale; "
    "and a confidence in [0,1]."
)

_FEW_SHOT = (
    "Examples.\n\n"
    "Function: def add(a: int, b: int) -> int  (metamorphic: add(a, b) == add(b, a)).\n"
    "Strategies:\n"
    "- a: st.integers(), confidence 0.95, rationale 'any int is valid'.\n"
    "- b: st.integers(), confidence 0.95, rationale 'any int is valid'.\n\n"
    "Function: def slugify(text: str) -> str  (idempotence; metamorphic case-insensitivity).\n"
    "Strategies:\n"
    "- text: st.text(), confidence 0.9, rationale 'any string is a valid input to a normalizer'.\n\n"
    "Function: def clamp(x: float, lo: float, hi: float) -> float  (value_postcondition "
    "lo <= result <= hi).\n"
    "Strategies:\n"
    "- x: st.floats(allow_nan=False), confidence 0.85, rationale 'any real number'.\n"
    "- lo: st.floats(allow_nan=False), confidence 0.85, rationale 'any real number'.\n"
    "- hi: st.floats(allow_nan=False), confidence 0.85, rationale 'any real number'.\n"
)


def _client() -> instructor.Instructor:
    """Build the Instructor-wrapped LiteLLM client.

    Factored out so tests can monkeypatch it with a fake that returns a known
    ``StrategyPlan`` instead of calling a real model.
    """
    return build_client()


def generate_strategies(
    meta: FunctionMetadata,
    analysis: PropertyAnalysis,
    settings: Settings | None = None,
    *,
    model_tier: str | None = None,
) -> StrategyPlan:
    """Generate a Hypothesis strategy per argument for one analyzed function.

    Raises ``PipelineError`` (stage "strategy_generation") if the LLM call fails for any
    reason — the caller's input was fine, so this surfaces as a 500 (D15).
    """
    settings = settings or get_settings()
    model = settings.model_for_tier(model_tier or settings.default_model_tier)

    if not settings.anthropic_api_key:
        raise PipelineError(_STAGE, "no LLM API key configured")

    detected = "; ".join(
        f"{p.property_class.value} [{p.relation}]" for p in analysis.detected
    ) or "(none)"
    types = ", ".join(
        f"{name}: {type_hint or 'unknown'}" for name, type_hint in analysis.input_types.items()
    ) or "(no arguments)"

    user_prompt = (
        "Write a Hypothesis strategy for each argument of this function.\n\n"
        f"Signature: {meta.signature}\n"
        f"Argument types: {types}\n"
        f"Return type: {analysis.return_type or '(none)'}\n"
        f"Docstring: {meta.docstring or '(none)'}\n"
        f"Detected properties: {detected}\n"
    )

    try:
        return _client().chat.completions.create(
            model=model,
            response_model=StrategyPlan,
            api_key=settings.anthropic_api_key,
            max_retries=settings.llm_max_retries,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT + "\n\n" + _FEW_SHOT},
                {"role": "user", "content": user_prompt},
            ],
        )
    except PipelineError:
        raise
    except Exception as exc:  # noqa: BLE001 — any LLM/transport failure becomes a 500
        raise PipelineError(_STAGE, str(exc)) from exc
```

---

## Step-by-step

### The imports and the stage label

```python
import instructor

from .config import Settings, get_settings
from .errors import PipelineError
from .llm import build_client
from .models import FunctionMetadata, PropertyAnalysis, StrategyPlan

_STAGE = "strategy_generation"
```

The same cast as unit 09, with two differences: it pulls the client builder from `.llm` (unit
10) instead of constructing it locally, and it works with `PropertyAnalysis` (the Phase 2 input)
and `StrategyPlan` (the Phase 3 output) from `models.py`. `_STAGE = "strategy_generation"` is the
single label for every `PipelineError` this file raises — the same one-source-of-truth habit as
`inference.py`'s `"property_detection"`.

### `_SYSTEM_PROMPT` — the standing instructions

The model's job description. Every clause is defending against a specific failure:

- **It defines the task narrowly:** "Produce ONE strategy per argument that generates VALID
  inputs — values in the function's intended domain." One strategy, per argument, valid.
- **It orders the reasoning:** "Reason from the declared TYPE first, then tighten ONLY when the
  name, docstring, or a detected property clearly implies a narrower domain." Types are the
  reliable signal; everything else is a careful refinement.
- **It bans over-constraining — the key risk:** "Do NOT over-constrain: when unsure, use the
  broad strategy for the type and a lower confidence — an over-narrow strategy hides bugs by
  never generating the inputs that trigger them." This is the single most important instruction
  in the file (see "What could go wrong").
- **It pins the output to real, runnable expressions:** "Each strategy must be a Python
  expression usable directly in @given(arg=<strategy>), using `st`," followed by a cheat-sheet
  of common type→strategy mappings. The next phase pastes these straight into a test, so prose
  won't do.
- **It provides an escape hatch for imports:** if a strategy needs something beyond `st`, list it
  in `extra_imports`.

### `_FEW_SHOT` — three worked examples

Three small demonstrations — `add` (two ints), `slugify` (a string), `clamp` (three floats) —
that anchor the *format* of the answer (argument, strategy expression, confidence, rationale)
better than instructions alone (decision **D25**). They're deliberately simple and broad, modelling
the "trust the type, stay broad" behaviour we want. The block is glued onto the system prompt when
the call is made.

### `_client()` — the swappable seam

```python
def _client() -> instructor.Instructor:
    return build_client()
```

A one-line wrapper over `build_client()` from unit 10. Why keep a local wrapper instead of
importing `build_client` and using it directly? Because `_client` is the **door the tests swap**:
a test does `monkeypatch.setattr(generation, "_client", ...)` to return a fake, so no real model
is ever called. The actual construction lives once in `llm.py`; the *seam* stays local to each
caller (the same arrangement `inference.py` uses).

### `generate_strategies(...)` — the one public entry point

```python
def generate_strategies(
    meta: FunctionMetadata,
    analysis: PropertyAnalysis,
    settings: Settings | None = None,
    *,
    model_tier: str | None = None,
) -> StrategyPlan:
```

It takes **two** inputs — the parsed function (`meta`, for the signature and docstring) and the
Phase 2 result (`analysis`, for the argument types and detected properties) — and returns a
`StrategyPlan`. As in unit 09, `settings` and `model_tier` are optional (the common call is just
`generate_strategies(meta, analysis)`), and `model_tier` is keyword-only thanks to the `*`.

**Resolve settings/model and guard the key** — identical to unit 09:

```python
settings = settings or get_settings()
model = settings.model_for_tier(model_tier or settings.default_model_tier)

if not settings.anthropic_api_key:
    raise PipelineError(_STAGE, "no LLM API key configured")
```

Pick the model for the tier (falling back to standard for anything unknown), and fail fast with a
clear `PipelineError` if there's no key — before building any prompt.

**Summarise the function for the model:**

```python
detected = "; ".join(
    f"{p.property_class.value} [{p.relation}]" for p in analysis.detected
) or "(none)"
types = ", ".join(
    f"{name}: {type_hint or 'unknown'}" for name, type_hint in analysis.input_types.items()
) or "(no arguments)"
```

These two lines turn the structured `PropertyAnalysis` into compact text the model can read.
`detected` becomes something like `idempotence [slugify(slugify(s)) == slugify(s)]; type_postcondition [isinstance(...)]`; `types` becomes `a: int, b: int`. The `or "(none)"` / `or
"(no arguments)"` tails keep the prompt readable when a list is empty, instead of printing a bare
blank. Feeding the model the **detected properties** (not just the types) is what lets it tighten
sensibly — e.g. a round-trip property hints that inputs must be well-formed for the inverse.

**Build the per-call question:**

```python
user_prompt = (
    "Write a Hypothesis strategy for each argument of this function.\n\n"
    f"Signature: {meta.signature}\n"
    f"Argument types: {types}\n"
    f"Return type: {analysis.return_type or '(none)'}\n"
    f"Docstring: {meta.docstring or '(none)'}\n"
    f"Detected properties: {detected}\n"
)
```

Today's task, assembled from the signature, the types, the return type, the docstring (the stated
intent), and the detected properties. Everything the system prompt told the model to reason from.

**Make the call:**

```python
return _client().chat.completions.create(
    model=model,
    response_model=StrategyPlan,
    api_key=settings.anthropic_api_key,
    max_retries=settings.llm_max_retries,
    max_tokens=settings.llm_max_tokens,
    temperature=settings.llm_temperature,
    timeout=settings.llm_timeout_seconds,
    messages=[
        {"role": "system", "content": _SYSTEM_PROMPT + "\n\n" + _FEW_SHOT},
        {"role": "user", "content": user_prompt},
    ],
)
```

The same call shape as unit 09, with `response_model=StrategyPlan` — Instructor forces the reply
into a `StrategyPlan` (a list of `GeneratedStrategy` + `extra_imports`) and re-asks on malformed
output (D13). All the tuning — tier, key, retries, token cap, temperature 0, timeout — comes from
`Settings` (unit 01).

Notice what's **not** here: there's no post-processing of the result. `generate_strategies`
returns the model's `StrategyPlan` **directly**. This is a deliberate contrast with
`infer_properties` (unit 09), which bolts AST types onto the model's output — because *there*, the
types are facts we already know. *Here*, the strategies **are** the generated content; there are no
extra AST facts to add, so we return what the model produced as-is (decision **D29**).

**Catch failures and relabel them as ours** — identical two-step `except` as unit 09: let our own
`PipelineError` fly through unchanged, and wrap *anything else* (network drop, provider error,
still-malformed reply) into a `PipelineError` for the `"strategy_generation"` stage, chaining the
original cause with `from exc`. No matter how the AI step fails, the caller sees one tidy 500 that
names the stage — never a raw library traceback.

---

## What could go wrong

### 1. Over-constraining the strategy (the cardinal sin)
If the model emits, say, `st.integers(min_value=0, max_value=100)` for an argument that's really
"any int," the test will *never* try the negative or huge values where the bug lives — and pass,
falsely. An over-tight strategy is worse than a loose one because it manufactures false
confidence. The prompt fights this hard ("reason from the type first, tighten only when clearly
justified, stay broad when unsure"), and `confidence` lets a later phase distrust a shaky
strategy. But it's the failure mode to watch, and the reason temperature is 0 (D25) — we don't
want the model getting "creative" with constraints.

### 2. A strategy that isn't valid for the function's domain
The opposite error: too *loose*. Feeding `st.text()` (any string) into `parse_iso_date` would
make almost every example raise, and the test would drown in irrelevant failures instead of
finding real bugs. Hence "valid inputs in the intended domain" — for a parser, well-formed text.
Balancing #1 and #2 is the whole craft of this step, which is exactly why it's an LLM call and not
a hard-coded `type → strategy` table.

### 3. A strategy that needs an import nobody added
`st.integers()` works with just `st`, but a strategy referencing `base64` or `datetime` would
fail at test time with a `NameError`. The `extra_imports` field is the model's channel to declare
those, so the later test-file phase can add them. If the model forgets, the generated test won't
run — a thing the Phase 4 generator (or a future validation step) will need to guard.

### 4. No coverage check yet (a known MVP gap)
`generate_strategies` returns whatever the model gives — it does **not** currently verify that
*every* argument actually received a strategy. A model that silently skips an argument would
produce an incomplete plan. This was a deliberate MVP omission (D29); asserting full coverage (and
re-asking or filling gaps) is a planned later refinement.

### 5. The usual AI-step risks (same as unit 09)
A missing key fails fast with a clear message; a present-but-invalid key, a hung call, or a
runaway reply are bounded by the key guard, the `timeout`, and `max_tokens`; and the whole thing
is tested offline by swapping `_client()` for a fake — so no test needs a network or a paid key.

---

## Summary

`generation.py` is TypeWright's **second** AI step and the heart of Phase 3.
`generate_strategies(meta, analysis)` takes a parsed function plus its Phase 2 property analysis
and returns a `StrategyPlan` — one Hypothesis strategy *expression* per argument (e.g.
`st.integers()`), plus any `extra_imports` — that Phase 4 will drop straight into `@given(...)`.
It's built to mirror `inference.py`: a system prompt + few-shot, one structured Instructor call at
temperature 0, the shared client from `llm.py` (unit 10) behind a local `_client()` test seam, and
every failure funnelled into a `PipelineError` for the `"strategy_generation"` stage → 500 (D15).
Its defining instruction is to generate **valid but not over-constrained** inputs — reason from
the type, tighten only when justified — because an over-tight strategy hides the very bugs the
test exists to find. Unlike unit 09 it returns the model's output **directly** (no AST bolt-on),
since the strategies are themselves the generated content (D29). It is standalone for now;
wiring it into `/v1/analyze` is a later unit (D28).

---

## Change history

- **2026-06-14** — Created in Phase 3, Unit 1. `generate_strategies(meta, analysis, settings=None,
  *, model_tier=None) -> StrategyPlan` makes one structured Instructor + LiteLLM call (via the
  shared `build_client()`, D27) at temperature 0 with few-shot (D25), turning a `PropertyAnalysis`
  into a per-argument Hypothesis `StrategyPlan` (D29). Returns the model's plan directly (no AST
  bolt-on, unlike `infer_properties`). A missing key and any call failure both raise
  `PipelineError("strategy_generation", …)` → 500 (D15). Standalone — not yet wired into
  `/v1/analyze` (D28). Mocked in tests (unit 07). Suite green at 34 passed.
- **2026-06-15** — Phase 4, Unit 1 refactor (D31): the structured call now goes through the shared
  `llm.complete(...)` (unit 10), hoisted once test generation became the third caller.
  `generate_strategies` no longer holds its own `create(...)` kwargs, missing-key check, or
  `try/except → PipelineError` block — it builds the prompt, then `return complete(_client, stage=
  "strategy_generation", …, response_model=StrategyPlan, …)`. The `from .errors import PipelineError`
  import was dropped. Behavior and the `generation._client` test seam are unchanged; the listing's
  call section above shows the pre-hoist inline form for teaching — the live file delegates to
  `complete`. Suite green at 45 passed.

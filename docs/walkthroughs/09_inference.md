# 09 — `src/typewright/inference.py`

## What this file is for

This file is where TypeWright **asks an AI to look at a function and recognise which
well-known kinds of property it should obey** — so a later phase can turn those into real
tests.

Up to now the program only *described* a function: its name, its arguments, its types — the
plain facts the parser could read straight off the page (unit 05). This file goes one step
further and asks a smarter question: *what kind of thing is this function, and what general
rule should hold for it?* Not "what does this exact body do," but "is this the sort of
function whose result, run through its inverse, gets you back to where you started? Is it the
sort where doing it twice is the same as doing it once?"

Think of an experienced mechanic glancing at a part. They don't re-derive it from scratch;
they *recognise* it: "ah, that's a one-way valve — water should only ever flow one
direction through it." That recognition — "this is a known **class** of thing, and known
classes come with known rules" — is exactly what we ask the AI to do here. We call each
known rule a **property class**, and the AI's job is recognition, not invention.

It produces one thing: `infer_properties(meta)` takes the parser's rich `FunctionMetadata`
and returns a `PropertyAnalysis` — the list of property classes the function appears to
satisfy, each with a concrete testable relation, plus the function's declared input/return
types for the phases that come next.

> **Heads-up if you read the old version of this walkthrough.** This file used to be about
> *contract inference* — asking the AI to write out a function's preconditions, postconditions,
> and invariants. That approach was replaced (decision **D23**); the "Why we recognise instead
> of inventing" section below explains exactly why, because it's the most important idea in the
> whole of Phase 2.

---

## Why we recognise classes instead of inventing a spec (the key idea — D23)

The first design of this step asked the AI to *read the function's body and write down the
rules it seemed to follow*. That sounds reasonable, and it's wrong in a subtle, fatal way.

If you derive the rule **from the body**, and then generate tests that check the body
**against that rule**, you've built a **circular oracle**. An "oracle" is the thing that
decides whether an output is right. Here the oracle *is* the implementation — so the tests
can only ever agree with whatever the code already does. Reverse the `+` to a `-` in a buggy
`add`, and the AI, reading that buggy body, would happily "infer" that the function subtracts
— and the generated test would pass. The tests catch crashes and nothing else. That defeats
the entire point of property-based testing, which is to catch *silent wrong answers*.

**Property classes break the circle.** A property class is a rule that holds for a *whole
family* of functions, independent of how any one of them is written:

- `parse(format(x)) == x` is true of every correct parse/format pair, no matter how `parse`
  is coded. Feed it a buggy `parse` and the equation *breaks* — exactly the signal we want.
- `sorted(sorted(xs)) == sorted(xs)` is true of every correct sort. It doesn't ask the body
  what it does; it states what *any* sort must do.

Because the rule comes from **outside** the implementation, the test can actually disagree
with the code — and disagreement is how bugs are found. So we don't ask the AI to be a
spec-writer (a job it's bad at and which is circular anyway). We ask it to be a
**classifier**: "which of these known, implementation-independent rules does this function
fit?" Recognition plays to an LLM's strengths; invention plays to its weaknesses. And when
it's only guessing, we want it to *say so* with a low confidence rather than fabricate a
rule to look useful.

---

## A mental model: a few ideas that make this file obvious

**1. An "LLM" is a model you ask in words and that answers in words.** LLM = *large language
model* — the kind of AI behind chat assistants. You give it text (a "prompt") and it writes
text back. That's powerful but loose: by default it might answer in a paragraph, a poem, or
JSON with the wrong field names. Much of this file is about *taming* that looseness into
something our program can rely on.

**2. LiteLLM is a universal adapter for talking to AI providers.** There are many AI
providers, each with its own slightly different way of being
called. **LiteLLM** is one library that speaks to all of them through *one* common shape, the
way a universal travel adapter lets one plug fit every country's socket. We talk to LiteLLM;
LiteLLM talks to the actual provider. Switching providers later becomes a one-line change
(decision **D17**). The provider is chosen by a prefix on the model name — the prefix on
`"anthropic/claude-sonnet-4-6"` is what tells LiteLLM where to route.

**3. Instructor turns a freeform reply into a strict, checked shape.** On its own, the model
might answer in prose. **Instructor** wraps the call and says: "don't just talk — fill in
*this* form," where the form is our `PropertyDetection` (a list of detected properties). If
the model's answer doesn't fit the form, Instructor hands it back and says "try again" —
automatically, a few times (those are the "reask retries," decision **D13**). So
`infer_properties` doesn't return a blob of text we have to pick apart; it returns a real,
validated object, or it fails cleanly. Picture a strict form at a government office: there
are exactly these boxes, and if you scribble outside them the clerk returns the form until
you fill it in properly.

**4. We turn the temperature down to zero.** "Temperature" is a dial on how random the
model's output is. High temperature = more inventive (good for brainstorming); low = more
stable and repeatable. Detection should give the *same* answer for the same function every
time, and low temperature also curbs the model's urge to invent a property just to seem
helpful — so we set it to `0.0` (decision **D25**). It pairs with the same anti-fabrication
rule that runs through the whole file.

**5. If our AI step breaks, that's *our* fault, not the caller's.** The person who sent us a
function did nothing wrong if the AI service is down or slow. So every failure here is
wrapped as a `PipelineError` (from unit 04), which the web layer turns into a **500** ("our
machinery broke"), naming `"property_detection"` as the stage that failed (decision **D15**).
This is the opposite of the parser's errors, which are the *caller's* fault and become 400s.

One more framing decision (**D19**): we ask for the detection in a **single** call. The
research TypeWright is based on describes a fancier loop — draft, critique, refine. That costs
more time, money, and code to test. We start with the simplest thing that produces a real
answer and can add the loop later *if* quality turns out to need it. Simpler beats cleverer
until proven otherwise.

---

## The seven property classes

The system prompt teaches the model a fixed menu of classes (and that a function may fit
several at once). It's worth knowing them, because they're the vocabulary of the whole
project from here on:

| Class | Plain meaning | Example relation |
|---|---|---|
| `round_trip` | an inverse exists — one-then-the-other returns the original | `from_base64(to_base64(d)) == d` |
| `idempotence` | doing it twice equals doing it once | `slugify(slugify(s)) == slugify(s)` |
| `invariant_preservation` | a structural fact of the output must hold vs the input | a sort keeps the same length and multiset |
| `metamorphic` | a relation between *changed* inputs and outputs, without knowing the exact output | `slugify(s) == slugify(s.upper())` |
| `type_postcondition` | the output matches the declared return type/shape | `isinstance(result, str)` |
| `value_postcondition` | a constraint on the output **value**, from the function's *intent* | `0 <= apply_discount(p, pct) <= p` |
| `totality` | the function shouldn't raise on inputs in its declared domain | (crash-only) |

Two of these carry special warnings, both baked into the prompt:

- **`value_postcondition` is on the tightest leash (D26).** It's the most powerful class —
  it's the one that catches plain business logic like `calculate_tax` or `clamp`, which the
  relational classes miss. But it's also the most fabrication-prone, because it's one short
  step from the circular oracle we just outlawed. So the rule is strict: derive the constraint
  **only** from the name, signature, and docstring (what the function is *supposed* to
  return), **never** from what the body computes. If the only basis would be the body's logic,
  the model is told to omit it or emit it with very low confidence.
- **`totality` is the weakest class.** "Doesn't crash" can't catch a wrong answer — only a
  crash. So the model is told to reach for it only when nothing stronger fits, and to keep its
  confidence modest. Prefer a real `value_postcondition` over a bare `totality` whenever
  possible.

---

## The whole file

```python
"""Phase 2: detect which well-known property classes a function satisfies.

``infer_properties`` takes the parser's ``FunctionMetadata`` and returns a
``PropertyAnalysis``: the property classes the function appears to satisfy
(round-trip, idempotence, invariant-preservation, metamorphic, type-postcondition,
value-postcondition, totality — D23/D24/D26), each with a concrete testable relation
and a confidence, plus the function's AST-declared input/return types for later
phases.

The model's job is RECOGNITION, not spec synthesis: it reasons from the name,
signature, type hints, and docstring and prefers recognized classes over invented
per-function specs, signalling uncertainty with low confidence rather than
fabricating (D23). The call goes through LiteLLM (D17), wrapped by Instructor so
the reply is coerced into ``PropertyDetection`` with reask retries (D13), at low
temperature (D25). Any failure of this stage becomes a ``PipelineError`` (stage
"property_detection", D15) -> HTTP 500.
"""

from __future__ import annotations

import instructor

from .config import Settings, get_settings
from .errors import PipelineError
from .llm import build_client
from .models import FunctionMetadata, PropertyAnalysis, PropertyDetection

_STAGE = "property_detection"

_SYSTEM_PROMPT = (
    "You are a property-based-testing assistant. You are given ONE Python function "
    "(name, signature, type hints, docstring, body). Do NOT restate what it does. "
    "Recognize which well-known PROPERTY CLASSES it plausibly satisfies, reasoning "
    "from its name, signature, type hints, and docstring. Prefer recognized classes "
    "over inventing a bespoke spec. If you are guessing rather than recognizing, say "
    "so with LOW confidence — never fabricate a property to seem useful.\n\n"
    "Property classes (a function may fit several):\n"
    "- round_trip: an inverse exists, so one-then-the-other returns the original "
    "(parse/format, encode/decode, serialize/deserialize, compress/decompress). Name "
    "the inverse in companion_function.\n"
    "- idempotence: doing it twice equals doing it once (normalize, sanitize, sort, "
    "dedup).\n"
    "- invariant_preservation: a structural fact of the output must hold vs the input "
    "(a sort keeps the same length and the same multiset of elements).\n"
    "- metamorphic: a relation between changed inputs and outputs without knowing the "
    "exact output (case/whitespace insensitivity, monotonicity, scaling, "
    "commutativity).\n"
    "- type_postcondition: the output matches the declared return type / shape.\n"
    "- value_postcondition: a constraint on the output VALUE that follows from the "
    "function's intent (a tax is >= 0, a discounted price is between 0 and the "
    "original, a probability is in [0,1]). TIGHTEST LEASH: derive the constraint ONLY "
    "from the name, signature, and docstring (what the function is SUPPOSED to "
    "return), NEVER from what the body computes — deriving it from the body is a "
    "circular oracle. If your only basis would be the body's logic, do NOT emit it (or "
    "emit with very low confidence). This is the most powerful class and the most "
    "prone to fabrication, so be strict and prefer low confidence when unsure. Prefer "
    "it over totality.\n"
    "- totality: the function should not raise on inputs in its declared domain. This "
    "is the WEAKEST class (crash-only — it cannot catch wrong answers); only emit it "
    "when nothing stronger applies, and keep its confidence modest.\n\n"
    "For each detected property give: property_class; a concrete, TESTABLE relation a "
    "later phase can turn straight into a test (e.g. 'parse(format(x)) == x', not "
    "'handles input correctly'); companion_function for round_trip; a short rationale; "
    "and a confidence in [0,1]. Return an empty list only if the function genuinely "
    "affords no property."
)

_FEW_SHOT = (
    "Examples.\n\n"
    "Function: def slugify(text: str) -> str  # lowercases, trims, replaces runs of "
    "non-alphanumerics with single hyphens.\n"
    "Detected:\n"
    "- idempotence, relation 'slugify(slugify(s)) == slugify(s)', confidence 0.95, "
    "rationale 'normalizer — re-running changes nothing'.\n"
    "- metamorphic, relation 'slugify(s) == slugify(s.upper())', confidence 0.8, "
    "rationale 'lowercasing makes it case-insensitive'.\n"
    "- type_postcondition, relation 'isinstance(slugify(s), str)', confidence 0.9.\n\n"
    "Function: def to_base64(data: bytes) -> str  (with an inverse from_base64).\n"
    "Detected:\n"
    "- round_trip, relation 'from_base64(to_base64(d)) == d', companion_function "
    "'from_base64', confidence 0.97, rationale 'encode/decode inverse pair'.\n\n"
    "Function: def apply_discount(price: float, pct: float) -> float  # price after a "
    "pct% discount.\n"
    "Detected:\n"
    "- value_postcondition, relation '0 <= apply_discount(price, pct) <= price', "
    "confidence 0.85, rationale 'a discount never raises the price or drops below "
    "zero — from intent, not the body'.\n"
    "- type_postcondition, relation 'isinstance(apply_discount(price, pct), float)', "
    "confidence 0.9.\n"
)


def _client() -> instructor.Instructor:
    """Build the Instructor-wrapped LiteLLM client.

    Factored out so tests can monkeypatch it with a fake that returns a known
    ``PropertyDetection`` instead of calling a real model.
    """
    return build_client()


def infer_properties(
    meta: FunctionMetadata,
    settings: Settings | None = None,
    *,
    model_tier: str | None = None,
) -> PropertyAnalysis:
    """Detect the property classes for one parsed function.

    Returns a ``PropertyAnalysis`` (detected properties + the function's AST types).
    Raises ``PipelineError`` (stage "property_detection") if the LLM call fails for
    any reason — the caller's input was fine, so this surfaces as a 500 (D15).
    """
    settings = settings or get_settings()
    model = settings.model_for_tier(model_tier or settings.default_model_tier)

    if not settings.anthropic_api_key:
        raise PipelineError(_STAGE, "no LLM API key configured")

    user_prompt = (
        "Detect the property classes for this function.\n\n"
        f"Signature: {meta.signature}\n"
        f"Docstring: {meta.docstring or '(none)'}\n\n"
        f"Source:\n{meta.source}"
    )

    try:
        detection = _client().chat.completions.create(
            model=model,
            response_model=PropertyDetection,
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

    return PropertyAnalysis(
        detected=detection.properties,
        input_types={arg.name: arg.type_hint for arg in meta.args},
        return_type=meta.return_type,
    )
```

---

## Step-by-step

### The imports and the stage label

```python
import instructor

from .config import Settings, get_settings
from .errors import PipelineError
from .llm import build_client
from .models import FunctionMetadata, PropertyAnalysis, PropertyDetection

_STAGE = "property_detection"
```

We pull in `instructor` (the strict-form layer, used here for the return-type hint) and the
things we already own: `Settings` for configuration (unit 01), `PipelineError` for the
our-fault failure (unit 04), `build_client` (the shared AI-client builder from `llm.py`,
unit 10), and three shapes from unit 03: `FunctionMetadata` (what comes in), `PropertyDetection`
(the raw shape the model fills in), and `PropertyAnalysis` (what we hand back). Note what
*moved out* in Phase 3: the direct `from litellm import completion` and the client construction
now live in `llm.py`, because strategy generation (unit 11) became a second caller and the
wiring belongs in one place (D27). The file still imports only *types and helpers*, not
provider-specific code — the provider brand is never named here; it only appears inside the
model-routing strings in `config.py`, which keeps this file provider-agnostic.

`from __future__ import annotations` is a small modern-Python nicety: it lets us write hints
like `Settings | None` freely by treating all hints as text.

`_STAGE = "property_detection"` is a single constant for the stage name, so every
`PipelineError` we raise (and the tests that check for it) use the exact same spelling. One
source of truth, no typos. (This string changed from the old `"contract_inference"` when the
approach was replaced — D23.)

### `_SYSTEM_PROMPT` — the standing instructions to the model

A "system prompt" is the model's job description — the rules it follows for *every* request,
as opposed to the specific function we send each time. Ours does several careful things, and
every one of them is defending against a specific failure mode:

- **It frames the job as recognition, not invention.** "Recognize which well-known PROPERTY
  CLASSES it plausibly satisfies… Prefer recognized classes over inventing a bespoke spec."
  This is D23 written as an instruction.
- **It lists the seven classes in plain words**, with examples for each, so the model has a
  fixed menu to classify into rather than an open-ended writing task.
- **It demands a *testable relation*, not prose.** "a concrete, TESTABLE relation a later
  phase can turn straight into a test (e.g. 'parse(format(x)) == x', not 'handles input
  correctly')." The next phase will turn these relations into actual code, so vague English
  is useless to it.
- **It puts `value_postcondition` on the tightest leash** — derive it from intent, never from
  the body — and marks `totality` as the weakest, last-resort class (D26).
- **It licenses honesty.** "If you are guessing rather than recognizing, say so with LOW
  confidence — never fabricate a property to seem useful," and "Return an empty list only if
  the function genuinely affords no property." Saying "I don't know" (a low confidence, or an
  empty list) is explicitly allowed, so the model isn't pushed into padding the answer with
  guesses.

### `_FEW_SHOT` — three worked examples

```python
_FEW_SHOT = ("Examples.\n\n...")
```

After the rules, we show the model three small, complete examples — `slugify`, `to_base64`,
and `apply_discount`. This is **few-shot prompting**: a couple of demonstrations teach the
desired *output shape* (a testable relation, a `companion_function` where relevant, a
confidence number) far more reliably than instructions alone (decision **D25**). The examples
are chosen to cover the interesting cases: a normaliser (idempotence + metamorphic + type),
an inverse pair (round-trip), and plain business logic (the intent-derived
`value_postcondition`). The few-shot block is glued onto the end of the system prompt when the
call is made.

### `_client()` — building the AI client, and why it's its own function

```python
def _client() -> instructor.Instructor:
    return build_client()
```

This delegates to `build_client()` in `llm.py` (unit 10), which does the actual
`instructor.from_litellm(completion)` wiring — wrapping Instructor around LiteLLM to get a client
that forces replies into the shape we ask for. As of Phase 3 that construction is shared (one
copy, two callers, D27), so this module keeps just a thin `_client()` over it.

Why keep a one-line wrapper at all instead of importing `build_client` and calling it directly?
**So the tests can replace it.** The leading
underscore (`_client`) is Python's gentle "this is internal" signal. Because the real call
lives behind this tiny door, a test can swap in a *fake* `_client` that returns a known
`PropertyDetection` without ever touching the network or needing an API key — exactly what
`test_inference.py` does (unit 07). Factoring it out costs one extra function and buys a fast,
free, deterministic test suite.

### `infer_properties(...)` — the one public entry point

```python
def infer_properties(
    meta: FunctionMetadata,
    settings: Settings | None = None,
    *,
    model_tier: str | None = None,
) -> PropertyAnalysis:
```

The signature reads like its job: *give me a parsed function, get back a property analysis.*
The two extra parameters both have defaults so the common call is just
`infer_properties(meta)`:

- `settings` — lets a caller (especially a test) pass in its own `Settings`. If omitted, we
  fetch the shared one. Passing it in is what makes the tests hermetic.
- `model_tier` — the `*` before it means it can only be given *by name*
  (`model_tier="premium"`), never by accident as a third positional argument. It chooses how
  powerful (and expensive) a model to use.

**Resolve the settings and the model:**

```python
settings = settings or get_settings()
model = settings.model_for_tier(model_tier or settings.default_model_tier)
```

`settings or get_settings()` means "use what I was handed, otherwise grab the shared one."
Then we turn a *tier* word into a concrete model name. The tiers are like service classes —
**economy** (cheap, fast: Haiku), **standard** (the balanced default: Sonnet), **premium**
(most capable: Opus). `model_for_tier` (unit 01) does the lookup and falls back to standard
for anything unrecognised, so a bad tier degrades gracefully instead of crashing.

**Guard the key before doing anything expensive:**

```python
if not settings.anthropic_api_key:
    raise PipelineError(_STAGE, "no LLM API key configured")
```

Talking to an AI provider needs an API key (a secret password). If it's missing, there's no
point building a prompt or attempting a call — we stop immediately with a clear
`PipelineError`. This is a "fail fast" check: catch the obvious problem at the door with a
readable message, rather than letting a confusing low-level error bubble up from deep inside
the library later.

**Build the question for this specific function:**

```python
user_prompt = (
    "Detect the property classes for this function.\n\n"
    f"Signature: {meta.signature}\n"
    f"Docstring: {meta.docstring or '(none)'}\n\n"
    f"Source:\n{meta.source}"
)
```

Where the system prompt was the standing job description, this is *today's task*: the actual
function. We hand the model the signature (the shape), the docstring (the stated *intent* —
which the `value_postcondition` rule leans on), and the full source. The `or '(none)'` keeps
the prompt tidy when a function has no docstring, rather than printing a bare `None`.

**Make the call — the heart of the file:**

```python
detection = _client().chat.completions.create(
    model=model,
    response_model=PropertyDetection,
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

Reading the arguments one by one:

- `model` — which model/provider to use (resolved above).
- `response_model=PropertyDetection` — **the magic word.** This is what makes Instructor force
  the reply into our `PropertyDetection` shape (a list of detected properties) and hand us back
  a real validated object instead of raw text.
- `api_key=...` — the secret, passed explicitly rather than relying on a hidden global, so
  configuration stays in one place (unit 01).
- `max_retries=...` — how many times Instructor may say "that didn't fit the form, try again"
  if the model's first answer is malformed (the reask retries of D13).
- `max_tokens=...` — a ceiling on the reply length. Property lists are small, so a small cap
  keeps cost and latency down.
- `temperature=...` — set to `0.0` (unit 01, D25): make detection stable and curb invention.
- `timeout=...` — give up after this many seconds, so a hung network call can't freeze a
  request forever.
- `messages=[...]` — the conversation, in the standard two-part shape: the `system` message
  (the job description **plus the few-shot examples**) followed by the `user` message (this
  function). The model reads both and answers.

**Catch failures and relabel them as ours:**

```python
except PipelineError:
    raise
except Exception as exc:  # noqa: BLE001 — any LLM/transport failure becomes a 500
    raise PipelineError(_STAGE, str(exc)) from exc
```

This two-step `except` is deliberate:

- The **first** clause catches our own `PipelineError` and simply lets it fly on unchanged —
  we don't want to wrap our clean error inside another.
- The **second** clause is the safety net: *anything else* that can go wrong — the network
  drops, the provider returns an error, the model's reply stays malformed even after all the
  retries — gets caught and re-raised as a `PipelineError` for the `"property_detection"`
  stage. The `from exc` keeps the original error chained underneath, so the real cause still
  shows up in the logs.

The comment `# noqa: BLE001` tells the linter "yes, we *meant* to catch broad `Exception`
here." Catching everything is usually a smell, but here it's the whole point: no matter *how*
the AI step fails, the caller should see one tidy 500 that names the stage — never a raw,
leaky stack trace from inside a third-party library.

**Assemble the analysis — and notice where the types come from:**

```python
return PropertyAnalysis(
    detected=detection.properties,
    input_types={arg.name: arg.type_hint for arg in meta.args},
    return_type=meta.return_type,
)
```

This last step is small but important. The model gave us only the `properties` list — that's
all we asked it for. The **types** (`input_types`, `return_type`) come straight from the
*AST* (`meta`), not the model. We already know them for certain from the parser, so there's no
reason to let the model echo or hallucinate them (decision **D24**). We bolt the model's
recognition together with the parser's facts into one self-contained `PropertyAnalysis` that
the next phase can consume without going back to the original metadata.

---

## Where this fits: it's wired into `/v1/analyze` now

A note for anyone who read the previous version of this doc: that version ended with a caveat
— "this engine exists and is tested, but the API doesn't call it yet." **That's no longer
true.** `POST /v1/analyze` now parses the function *and* calls `infer_properties`, returning
`{ analysis_id, function, properties }`. The route reaches this step through a FastAPI
dependency (`get_infer_properties`) so tests can swap in a fake with no live key — see unit 06
(`main.py`) and the `test_api.py` section of unit 07. So today, if you POST a function, you
get its detected properties back in the response.

---

## What could go wrong

### 1. The circular oracle (the mistake this whole design avoids)
The single biggest trap in this kind of tool is deriving the rule from the body and then
testing the body against it — which can only ever pass. The entire pivot to property *classes*
(D23) exists to avoid it, and the prompt reinforces it most sharply for `value_postcondition`:
derive the constraint from *intent*, never from the body. If you ever find yourself tempted to
let the model "read the code and tell us what it guarantees," that's the circle re-forming.

### 2. The model invents a property that isn't really there
LLMs are confident even when wrong — they can "hallucinate" a property a function doesn't
actually have. We can't fully prevent it, but three forces push against it: the prompt tells
the model to use **low confidence** when guessing and to return an **empty list** when nothing
fits; the **temperature is 0** (D25) so it isn't reaching for novelty; and every detected
property carries a `confidence` number (unit 03) that later phases can threshold on. The goal
isn't a model that never guesses — it's a model that *labels* its guesses honestly.

### 3. A missing or wrong API key crashing deep in the library
Without the up-front `if not settings.anthropic_api_key` check, a missing key would still fail
— but with a confusing error from deep in LiteLLM. The early guard turns that into one clear
sentence at the front door. (A key that's *present but invalid* is caught instead by the broad
`except`, and still becomes a clean 500.)

### 4. A blurry line between "our fault" and "the caller's fault"
It would be easy to let some raw exception escape and have the web layer guess at a status
code. By funnelling *every* failure here through `PipelineError`, we keep the rule crisp:
property detection breaking is **always** a 500, because the caller's function was fine. The
parser's errors stay 400s; this stays 500. No guessing at the edge.

### 5. Tests that secretly need the internet (or a paid key)
If `infer_properties` only ever called the real model, the test suite would be slow, cost
money, need a secret in CI, and flake whenever the provider hiccuped. Hiding the real call
behind `_client()` lets tests swap in a fake — so they run in milliseconds, for free, and give
the same answer every time. The price is one tiny extra function; the payoff is a suite anyone
can run offline.

### 6. A runaway or hanging call
An AI call is a network call to someone else's computer; it can be slow or never answer.
`timeout` caps how long we wait, and `max_tokens` caps how much it can write back. Both keep a
single analysis from quietly costing a fortune or hanging a request forever.

---

## Summary

`inference.py` is TypeWright's first AI step. `infer_properties(meta)` takes a parsed function
and returns a `PropertyAnalysis` — the well-known **property classes** the function appears to
satisfy (round-trip, idempotence, invariant-preservation, metamorphic, type- and
value-postcondition, totality), each with a concrete *testable relation* and a confidence,
plus the function's declared types straight from the AST. The crucial design choice is
**recognition over invention** (D23): deriving rules from the body and testing the body
against them is a circular oracle that catches only crashes, so instead we ask the model to
classify the function into implementation-independent rules that can actually disagree with
buggy code. It reaches the model through **LiteLLM** (one adapter for many providers, D17) and
wraps the call in **Instructor**, which forces the reply into the `PropertyDetection` shape and
retries on malformed output (D13), at **temperature 0** with **few-shot examples** (D25). The
`value_postcondition` class is the most powerful and rides the tightest leash — intent only,
never the body (D26). Configuration all comes from `Settings` (unit 01); the real client lives
behind `_client()` so tests run offline; and every failure becomes one honest `PipelineError`
for the `"property_detection"` stage → 500 (D15). It is now wired into `/v1/analyze` (unit 06).

---

## Change history

- **2026-06-12** — Created in Phase 2, Unit 2 as `infer_contract`: one structured Instructor +
  LiteLLM call returning a `Contract` (preconditions/postconditions/invariants), with the live
  client behind `_client()` for test stubbing (D20). Stage name `"contract_inference"`.
- **2026-06-13** — **Replaced contract inference with property-class DETECTION (D23).**
  `infer_contract -> infer_properties`; it now returns a `PropertyAnalysis` (detected classes +
  AST-declared types, D24) instead of a `Contract`. Reworked the system prompt to *recognise*
  the seven property classes rather than synthesise a spec, added three few-shot examples and
  set `temperature = 0.0` (D25), and added the intent-only `value_postcondition` class on the
  tightest leash (D26). The model now returns only a `PropertyDetection` (the properties list);
  the function bolts on `input_types`/`return_type` from the AST. Pipeline stage renamed
  `"contract_inference" -> "property_detection"`. The step is also now wired into
  `POST /v1/analyze` (unit 06). Suite green at 30 passed.
- **2026-06-14** — Phase 3, Unit 1 refactor (D27): the Instructor/LiteLLM client construction
  moved out to the shared `llm.py` (unit 10) now that strategy generation (unit 11) is a second
  caller. `inference.py` dropped `from litellm import completion` and its `_client()` now returns
  `build_client()`; behavior and the `inference._client` test seam are unchanged. No change to
  `infer_properties` itself. Suite green at 34 passed.
- **2026-06-15** — Phase 4, Unit 1 refactor (D31): the structured *call* itself now goes through
  the shared `llm.complete(...)` (unit 10), hoisted once test generation became the third caller.
  `infer_properties` no longer holds its own `create(...)` kwargs, the missing-key check, or the
  `try/except → PipelineError` block — it builds the prompt, calls `complete(_client, stage=
  "property_detection", …, response_model=PropertyDetection, …)`, then wraps the result into a
  `PropertyAnalysis` as before. The `from .errors import PipelineError` import was dropped (the
  helper raises it now). Behavior and the `inference._client` seam are unchanged; the listing's
  call section above shows the pre-hoist inline form for teaching — the live file delegates to
  `complete`. Suite green at 45 passed.

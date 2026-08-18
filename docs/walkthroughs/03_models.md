# 03 — `src/typewright/models.py`

## What this file is for

This file describes the **shapes of the data** that move through TypeWright. It doesn't
*do* anything — it doesn't parse code or call any AI. It just says, very precisely, "a
request looks like this", "a parsed function looks like that", "a response looks like
this other thing".

Think of it like the set of labeled boxes in a warehouse. Before anything gets shipped,
you decide: this box holds shoes, that box holds books, each with a label listing exactly
what's allowed inside. If someone tries to put a banana in the "shoes" box, it's rejected
at the door. These models are those labeled boxes — and the library that enforces the
labels is **Pydantic**.

## A mental model: what does Pydantic give us?

**Pydantic** is a library that turns a plain Python class into a *validated* data
container. You list the fields and their types; Pydantic then guarantees that any object
of that class actually matches. If the web request is missing a required field, or sends
a number where text was expected, Pydantic catches it and produces a clear error — before
that bad data can reach our logic.

This matters a lot for a web service: data arrives from the outside world (untrusted), and
we want to reject malformed input at the very edge, loudly and clearly, instead of letting
it cause a confusing crash deep inside.

## The two audiences (the key idea in this file)

There are **two kinds of model here, kept deliberately separate**:

1. **Internal models** — `FunctionMetadata` (the full, rich description of a parsed
   function: name, every argument, return type, docstring, decorators, async-ness, and the
   complete source text) and `PropertyAnalysis` (the Phase 2 result: which property classes
   the function satisfies, plus its types). Later phases need this full picture.

2. **API-facing models** — `AnalyzeRequest` and `AnalyzeResponse`. These are what the
   outside world sends and receives. They expose only what the current phase can *honestly*
   deliver.

Why split them? Two reasons:
- **Honesty (DECISIONS.md D5):** the full response in the project brief has fields like
  `bugs_found` and `fix_suggestion`. Those features don't exist yet, so we don't put them in
  the response. Showing `bugs_found: []` today would falsely imply "we tested and found none".
- **Safety (DECISIONS.md D6):** internal details — like the raw function source — shouldn't
  accidentally leak out through the API. Keeping a separate "view" model means the API can
  only ever return the fields we chose.

> **If you read the old version of this doc:** Phase 2 used to define a `Contract` model
> (three lists: preconditions / postconditions / invariants). That was replaced by the
> **property-class** models below (decision **D23**) — the *why* lives in unit 09; in short,
> inferring a spec from the body and testing the body against it is a circular oracle. The
> response field that grew out of this is now `properties`, not `contract`.

## The whole file

```python
"""Pydantic models: the typed shapes of data flowing through TypeWright.

Two audiences are kept separate on purpose (DECISIONS.md D5, D6):

- Internal: ``FunctionMetadata`` is the *rich* description the AST parser produces.
  Later phases (property detection, strategy generation) need the full picture,
  including the function's source.
- API-facing: ``AnalyzeRequest`` / ``AnalyzeResponse`` expose only what a phase can
  honestly provide. We do NOT include fields for features that don't exist yet
  (bugs_found, fix_suggestion) — those arrive in their own phases.
"""

import enum

from pydantic import BaseModel, Field


class ArgKind(str, enum.Enum):
    """How an argument is passed to the function."""

    POSITIONAL_OR_KEYWORD = "positional_or_keyword"
    POSITIONAL_ONLY = "positional_only"
    KEYWORD_ONLY = "keyword_only"
    VAR_POSITIONAL = "var_positional"  # the *args parameter
    VAR_KEYWORD = "var_keyword"  # the **kwargs parameter


class Argument(BaseModel):
    """One parameter of a function."""

    name: str
    type_hint: str | None = None
    default: str | None = None
    kind: ArgKind = ArgKind.POSITIONAL_OR_KEYWORD


class FunctionMetadata(BaseModel):
    """Rich, internal representation of a parsed function (produced by the parser)."""

    name: str
    args: list[Argument]
    return_type: str | None = None
    docstring: str | None = None
    is_async: bool = False
    decorators: list[str] = Field(default_factory=list)
    signature: str
    source: str


class PropertyClass(str, enum.Enum):
    """Well-known property classes a function can satisfy (a function may fit several)."""

    ROUND_TRIP = "round_trip"
    IDEMPOTENCE = "idempotence"
    INVARIANT_PRESERVATION = "invariant_preservation"
    METAMORPHIC = "metamorphic"
    TYPE_POSTCONDITION = "type_postcondition"
    VALUE_POSTCONDITION = "value_postcondition"  # output-value constraint from INTENT only — most powerful, most circular-prone; tightest leash (D26)
    TOTALITY = "totality"  # weakest: crash-only, cannot catch wrong answers


class DetectedProperty(BaseModel):
    """One property class the function appears to satisfy (LLM-detected, Phase 2).

    The LLM recognizes which well-known class fits rather than inventing a bespoke
    spec (D23). ``confidence`` is low when it is guessing rather than recognizing.
    """

    property_class: PropertyClass
    relation: str = Field(
        ...,
        description=(
            "A concrete, TESTABLE relation a later phase turns straight into a "
            "test, e.g. 'parse(format(x)) == x' — not vague prose."
        ),
    )
    companion_function: str | None = Field(
        default=None,
        description="For round_trip: the inverse function's name (e.g. 'format').",
    )
    rationale: str = Field(..., description="Why this property is expected. Brief.")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="0–1; low when guessing rather than recognizing.",
    )


class PropertyDetection(BaseModel):
    """The LLM's raw structured output: the property classes it recognized."""

    properties: list[DetectedProperty] = Field(default_factory=list)


class PropertyAnalysis(BaseModel):
    """Phase 2 result: detected properties plus the AST-declared types later phases
    need to build strategies (D23, D24). Replaces the earlier ``Contract`` model.
    """

    detected: list[DetectedProperty] = Field(default_factory=list)
    input_types: dict[str, str | None] = Field(default_factory=dict)
    return_type: str | None = None


class GeneratedStrategy(BaseModel):
    """One Hypothesis strategy for a single function argument (Phase 3, D29).

    ``strategy`` is a Python expression usable directly in ``@given(arg=<strategy>)``,
    e.g. ``"st.integers()"`` or ``"st.text(min_size=1)"``. ``confidence`` is low when
    the model is guessing a domain rather than reading it off the type.
    """

    argument: str = Field(..., description="The parameter this strategy generates values for.")
    strategy: str = Field(
        ...,
        description="A Hypothesis strategy expression, e.g. 'st.integers()' — usable in @given.",
    )
    rationale: str = Field(..., description="Why this strategy fits the argument. Brief.")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="0–1; low when guessing the domain rather than reading the type.",
    )


class StrategyPlan(BaseModel):
    """Phase 3 result: one Hypothesis strategy per argument, plus any extra imports.

    The LLM returns this directly (D29). ``extra_imports`` lists anything a strategy needs
    beyond ``from hypothesis import strategies as st`` (e.g. ``"import base64"``).
    """

    strategies: list[GeneratedStrategy] = Field(default_factory=list)
    extra_imports: list[str] = Field(default_factory=list)


class GeneratedTests(BaseModel):
    """The LLM's raw Phase 4 output (hybrid assembly, D32): the per-property test
    functions it wrote, the extra imports they need, and any property it could not make
    executable.

    Only the test functions come from the model; ``testgen.py`` assembles the final file
    around them (import header + the verbatim function under test). Each ``test_functions``
    item is a complete ``@given``-decorated function as source code.
    """

    test_functions: list[str] = Field(
        default_factory=list,
        description=(
            "Each item is a complete @given-decorated pytest function as source code, "
            "asserting ONE detected relation. Do NOT include the function under test or "
            "the hypothesis/pytest imports."
        ),
    )
    extra_imports: list[str] = Field(
        default_factory=list,
        description="Imports the TESTS need beyond hypothesis/pytest, e.g. 'import math'.",
    )
    skipped: list[str] = Field(
        default_factory=list,
        description=(
            "Properties left untested and why, e.g. "
            "'round_trip: companion from_base64 not in the snippet'."
        ),
    )


class GeneratedTestFile(BaseModel):
    """Phase 4 result (D33/D35): a self-contained, syntactically-valid pytest module.

    ``source`` is the complete file (imports + the function under test + the @given tests)
    that runs under pytest as-is. ``test_names`` are read off the parsed AST (not trusted
    from the LLM). ``skipped`` records properties left untested and why — e.g. a round-trip
    whose inverse is not in the snippet (PROJECT_BRIEF §8 risk 3).
    """

    source: str
    test_names: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


class AnalyzedFunction(BaseModel):
    """The lean, API-facing view of a parsed function."""

    name: str
    signature: str
    args: list[Argument]
    return_type: str | None = None
    docstring: str | None = None

    @classmethod
    def from_metadata(cls, meta: FunctionMetadata) -> "AnalyzedFunction":
        """Project the rich internal metadata down to the fields we expose."""
        return cls(
            name=meta.name,
            signature=meta.signature,
            args=meta.args,
            return_type=meta.return_type,
            docstring=meta.docstring,
        )


class AnalyzeRequest(BaseModel):
    """Body of POST /v1/analyze."""

    code: str = Field(..., description="Python source containing the function to analyze.")
    function_name: str | None = Field(
        default=None,
        description=(
            "Which function to analyze. Optional when the source has exactly one "
            "top-level function."
        ),
    )
    model_tier: str | None = Field(
        default=None,
        description=(
            "Model tier for property detection: 'economy', 'standard', or "
            "'premium'. Optional; falls back to the configured default, and an "
            "unknown tier degrades to 'standard' (D17). Other spec'd request "
            "fields (include_fix_suggestion, max_test_runtime_seconds) arrive "
            "with the phases that use them (D22)."
        ),
    )


class AnalyzeResponse(BaseModel):
    """Response of POST /v1/analyze.

    Phase 4: the parsed ``function``, the ``properties`` it appears to satisfy (D23),
    the ``strategy_plan`` — a Hypothesis strategy per argument (D29, D30) — and the
    ``test_file``: a complete, runnable pytest module asserting those properties (D33,
    D36). The later fields the spec promises (``bugs_found``, ``fix_suggestion``,
    ``metadata``) appear only once their phases make them real (D5).
    """

    analysis_id: str
    function: AnalyzedFunction
    properties: PropertyAnalysis
    strategy_plan: StrategyPlan
    test_file: GeneratedTestFile
```

## Step-by-step

### `ArgKind` — the five ways to pass an argument

```python
class ArgKind(str, enum.Enum):
    POSITIONAL_OR_KEYWORD = "positional_or_keyword"
    POSITIONAL_ONLY = "positional_only"
    KEYWORD_ONLY = "keyword_only"
    VAR_POSITIONAL = "var_positional"  # the *args parameter
    VAR_KEYWORD = "var_keyword"  # the **kwargs parameter
```

An **enum** is a fixed menu of allowed values — `kind` can only ever be one of these five,
never a random typo'd string. Inheriting from `str` as well means each value *is* its text,
so when it's turned into JSON it shows up simply as `"keyword_only"`.

Why five kinds? Python lets you write parameters in five different ways:

```python
def f(a, /, b, *args, c, **kwargs):
#       ^         ^      ^     ^
#  a: positional-only (before the /)
#  b: positional-or-keyword (the normal kind)
#  *args: collects extra positional arguments  -> VAR_POSITIONAL
#  c: keyword-only (after the *args)
#  **kwargs: collects extra keyword arguments  -> VAR_KEYWORD
```

We capture the kind now because later phases must treat `*args`/`**kwargs` differently from
ordinary parameters when generating test inputs.

### `Argument` — one parameter, described

```python
class Argument(BaseModel):
    name: str
    type_hint: str | None = None
    default: str | None = None
    kind: ArgKind = ArgKind.POSITIONAL_OR_KEYWORD
```

Each parameter becomes one of these. Notes:

- `type_hint: str | None = None` — the type annotation **as text** (e.g. `"int"`), or
  `None` if the parameter had no annotation. The `| None` means "this may be absent". The
  `= None` makes that the default.
- We store the type and default as **strings** on purpose. We're *describing source code*,
  not running it. `"int"` here is just the characters `i-n-t`, not Python's actual `int`.
- `kind` defaults to the normal `POSITIONAL_OR_KEYWORD`, which is by far the most common.

### `FunctionMetadata` — the rich internal picture

```python
class FunctionMetadata(BaseModel):
    name: str
    args: list[Argument]
    return_type: str | None = None
    docstring: str | None = None
    is_async: bool = False
    decorators: list[str] = Field(default_factory=list)
    signature: str
    source: str
```

This is everything the parser will learn about a function. Two details worth calling out:

- `decorators: list[str] = Field(default_factory=list)` — this gives each new object a
  **fresh empty list**. You must never write `= []` as a default in Python: that single
  list would be silently *shared* by every instance, so adding to one would change them all.
  `default_factory=list` builds a new one each time. (A famous Python footgun, avoided here.)
- `source` holds the function's full text. It lives in the *internal* model — and, as we'll
  see, is intentionally **not** exposed in the API view. But it *is* what the property-detection
  step (unit 09) sends to the AI.

### The property models (Phase 2) — the heart of the rewrite

This is where Phase 2 lives. Four small classes, building up from the inside out. The big
idea behind all of them (decision **D23**, explained fully in unit 09): instead of asking the
AI to *write a spec* for a function, we ask it to **recognise which well-known kinds of
property** the function fits — rules like "running it twice is the same as once" that hold for
a whole family of functions, independent of how any one is coded.

#### `PropertyClass` — the fixed menu of property kinds

```python
class PropertyClass(str, enum.Enum):
    ROUND_TRIP = "round_trip"
    IDEMPOTENCE = "idempotence"
    INVARIANT_PRESERVATION = "invariant_preservation"
    METAMORPHIC = "metamorphic"
    TYPE_POSTCONDITION = "type_postcondition"
    VALUE_POSTCONDITION = "value_postcondition"
    TOTALITY = "totality"
```

Like `ArgKind`, this is an enum — a closed menu. The AI must classify a function into *these*
seven named buckets, not make up its own. (Unit 09 lists what each one means and gives an
example relation.) Two carry comments that matter:

- **`value_postcondition`** — a constraint on the output *value* (e.g. `result >= 0`),
  derived from the function's **intent** (its name/docstring), never from what the body
  computes. It's the most powerful class (it's the only one that catches plain business logic
  like `calculate_tax`), and the most fabrication-prone — hence the "tightest leash" (D26).
- **`totality`** — "doesn't crash on valid input." The weakest class, because not-crashing
  can't catch a *wrong answer*. Used only when nothing stronger fits.

#### `DetectedProperty` — one recognised property, fully described

```python
class DetectedProperty(BaseModel):
    property_class: PropertyClass
    relation: str = Field(..., description="A concrete, TESTABLE relation ...")
    companion_function: str | None = Field(default=None, description="For round_trip: ...")
    rationale: str = Field(..., description="Why this property is expected. Brief.")
    confidence: float = Field(..., ge=0.0, le=1.0, description="0–1; low when guessing ...")
```

This is the box the AI fills in once per property it spots. Each field earns its place:

- **`property_class`** — which of the seven kinds this is.
- **`relation`** — the crucial field: a *concrete, testable* statement like
  `parse(format(x)) == x`, **not** vague prose like "handles input correctly". Phase 3 will
  turn this string straight into a real test, so it has to be executable-shaped.
- **`companion_function`** — only meaningful for `round_trip`: the name of the inverse (e.g.
  `format` pairs with `parse`). `None` for everything else, so it's optional.
- **`rationale`** — a short "why I think so", useful for a human reading the result.
- **`confidence`** — a number from 0 to 1, and the `ge=0.0, le=1.0` makes Pydantic *enforce*
  that range (anything outside is rejected). This is the **anti-fabrication signal**: when the
  AI is guessing rather than recognising, it's told to put a low number here, and later phases
  can choose to ignore low-confidence properties.

#### `PropertyDetection` — what the AI literally returns

```python
class PropertyDetection(BaseModel):
    properties: list[DetectedProperty] = Field(default_factory=list)
```

This is deliberately tiny: *just* the list of detected properties, nothing else. It's the
exact shape we hand to Instructor as the "fill in this form" target in unit 09. Why so bare?
Because we only want the AI to give us the one thing it's good at — the recognition. Anything
we already know for certain (the function's types) we do **not** ask it for, so it can't get
them wrong. The default empty list means "no properties found" is a perfectly valid answer.

#### `PropertyAnalysis` — the assembled Phase 2 result

```python
class PropertyAnalysis(BaseModel):
    detected: list[DetectedProperty] = Field(default_factory=list)
    input_types: dict[str, str | None] = Field(default_factory=dict)
    return_type: str | None = None
```

This is the finished product of unit 09 and the thing the API returns. It bolts two sources
together:

- **`detected`** — the AI's recognised properties (copied from `PropertyDetection.properties`).
- **`input_types` / `return_type`** — the function's declared types, taken **from the parser's
  AST, not the AI**. `input_types` maps each parameter name to its type-hint text (or `None`).

That split is the whole point of `PropertyAnalysis` (decision **D24**): the *recognition*
comes from the model, the *facts* come from the AST, and the result is one self-contained
payload that Phase 3 (strategy generation) can read without going back to the original
metadata. It replaces the old `Contract` model entirely.

### The strategy models (Phase 3) — the output of unit 11

Phase 3 consumes a `PropertyAnalysis` and produces these two models: the recipe for generating
test inputs. (The generator itself is unit 11; here we just describe its output shape, decision
**D29**.)

#### `GeneratedStrategy` — one Hypothesis strategy for one argument

```python
class GeneratedStrategy(BaseModel):
    argument: str = Field(..., description="The parameter this strategy generates values for.")
    strategy: str = Field(..., description="A Hypothesis strategy expression ... usable in @given.")
    rationale: str = Field(..., description="Why this strategy fits the argument. Brief.")
    confidence: float = Field(..., ge=0.0, le=1.0, description="0–1; low when guessing ...")
```

This is the box the AI fills in once per argument. The fields deliberately echo
`DetectedProperty`:

- **`argument`** — which parameter this strategy is for (e.g. `"a"`).
- **`strategy`** — the crucial field: a **Hypothesis strategy expression as text**, like
  `"st.integers()"` or `"st.text(min_size=1)"`. It's stored as a string because, just like the
  type hints, we're *describing code for a later phase to use* — Phase 4 will drop this string
  straight into `@given(arg=<strategy>)`.
- **`rationale`** — a brief "why this strategy fits".
- **`confidence`** — the same range-checked 0–1 number as on `DetectedProperty`, and the same
  `ge=0.0, le=1.0` enforcement. It's low when the model is guessing a domain rather than reading
  it cleanly off the type — and a later phase can distrust a shaky strategy.

> **A real footgun, caught by exactly this field.** When this class was first applied, a paste
> slip typed `confidence: str` instead of `confidence: float`. Because the type is the contract,
> Pydantic then *rejected* a perfectly good `0.95` ("Input should be a valid string"), and the
> test failed instantly with a precise message — which is the whole point of declaring types:
> the wrong one fails loudly and immediately, not silently later. One-character fix
> (`str → float`), and it's why the suite is the safety net.

#### `StrategyPlan` — all the strategies, plus imports

```python
class StrategyPlan(BaseModel):
    strategies: list[GeneratedStrategy] = Field(default_factory=list)
    extra_imports: list[str] = Field(default_factory=list)
```

The finished Phase 3 result and the thing the generator hands back. `strategies` is one
`GeneratedStrategy` per argument; `extra_imports` lists anything a strategy needs beyond the
standard `from hypothesis import strategies as st` (e.g. `"import base64"`), so the test-file
phase knows what to add at the top. Unlike `PropertyAnalysis`, there's no AST data bolted on
here — the strategies *are* the generated content, so the model's `StrategyPlan` is returned
as-is (D29). Both fields default to empty lists (the same fresh-list trick), so "no strategies"
is a valid, safe answer.

### The test-file models (Phase 4) — the output of unit 12

Phase 4 turns the properties and strategies into an actual pytest file. It uses **two** boxes,
on purpose, because two different things happen: the AI hands back raw material, and then *we*
build the finished file from it.

#### `GeneratedTests` — the AI's raw material

```python
class GeneratedTests(BaseModel):
    test_functions: list[str] = Field(default_factory=list)
    extra_imports: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
```

This is what the **AI** returns. `test_functions` is the list of test functions it wrote (each a
complete `@given`-decorated function, as a string of source code). `extra_imports` is anything
those tests need beyond Hypothesis and pytest (e.g. `"import math"`). `skipped` lists properties
it *couldn't* turn into a runnable test, with a reason — most often a round-trip whose partner
("inverse") function isn't in the snippet. Notice what's **not** here: the imports header and the
function under test. The AI deliberately doesn't write those — `testgen.py` adds them itself
(decision **D32**), so the parts that must be exactly right aren't left to the model.

#### `GeneratedTestFile` — the finished file

```python
class GeneratedTestFile(BaseModel):
    source: str
    test_names: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
```

This is what `generate_test_file` *returns*. `source` is the whole, self-contained pytest file
as one string (header + the real function + the tests), ready to run (D33). `test_names` are the
tests actually present — read straight off the parsed file (Python's `ast`), not taken from the
AI's word, so they're a fact about the file rather than a claim. `skipped` is carried through from
`GeneratedTests` so the honest "couldn't cover these" list survives into the result. Splitting raw
input (`GeneratedTests`) from finished output (`GeneratedTestFile`) keeps the "AI writes the tests,
we build the file" seam clean (D32/D35).

### The bug models (Phase 5) — the output of unit 15

Once the generated tests have actually *run* in the sandbox, the result-parser (unit 15) reports
what it found using three shapes (decision **D40**):

```python
class BugSeverity(str, enum.Enum):
    CRASH = "crash"  # the function raised an uncaught, non-assertion exception
    PROPERTY_VIOLATION = "property_violation"  # an asserted relation failed — a silent wrong answer


class Bug(BaseModel):
    test_name: str
    failing_input: str
    error: str
    violated_property: str
    severity: BugSeverity


class BugReport(BaseModel):
    bugs: list[Bug] = Field(default_factory=list)
    timed_out: bool = False
    exit_code: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    output_truncated: bool = False
```

A **`Bug`** is one falsified property: the `test_name` that failed, the `failing_input` Hypothesis
found (e.g. `"x='A'"`), the `error` (the exception type — `"AssertionError"` for a violated relation,
else the crash type like `"IndexError"`), the `violated_property` (the actual *relation* the test was
checking, not just a label), and a `severity`. **`BugSeverity`** is deliberately just two values:
`property_violation` is the valuable kind — the function returned a *wrong answer* without crashing —
and `crash` is when it threw. (A third "error" bucket was considered and left out for now, D40.)

`test_name` is one field beyond the four the spec sketches; it's real and handy for tracing a bug back
to its test, which the D5 "don't expose unreal fields" rule allows (it forbids *fake* fields, not extra
true ones).

A **`BugReport`** wraps the whole run: the `bugs` list plus the outcome facts the API layer needs —
`timed_out` (so it can answer 504), `exit_code`, the passed/failed `tests_*` counts, and
`output_truncated` (a warning that pytest's output was cut off and the picture may be incomplete). The
public API will surface only the `bugs` list (as `bugs_found`) when unit 06 wires this in; the rest of
`BugReport` is internal bookkeeping.

### `AnalyzedFunction` — the lean API view

```python
class AnalyzedFunction(BaseModel):
    name: str
    signature: str
    args: list[Argument]
    return_type: str | None = None
    docstring: str | None = None

    @classmethod
    def from_metadata(cls, meta: FunctionMetadata) -> "AnalyzedFunction":
        return cls(
            name=meta.name,
            signature=meta.signature,
            args=meta.args,
            return_type=meta.return_type,
            docstring=meta.docstring,
        )
```

This is the function half of what the API returns. Compare it to `FunctionMetadata`: no
`source`, no `decorators`, no `is_async`. Those are internal-only.

`from_metadata` is a **classmethod** — a function that builds a new `AnalyzedFunction` from a
`FunctionMetadata`. Putting this conversion in one named place means there's exactly one spot
that decides "what goes out to the world", instead of that logic being scattered around the
route handler. If we ever want to expose a new field, we change it here, once.

### `AnalyzeRequest` — what the caller sends

```python
class AnalyzeRequest(BaseModel):
    code: str = Field(..., description="Python source containing the function to analyze.")
    function_name: str | None = Field(default=None, description="...")
    model_tier: str | None = Field(default=None, description="economy | standard | premium ...")
```

- `code` is **required**. The `...` (literally three dots, Python's "Ellipsis") inside
  `Field(...)` means "no default — the caller must provide this". If it's missing, Pydantic
  rejects the request with a clear error.
- `function_name` is optional. If the submitted code has exactly one top-level function, we
  can figure out which one to analyze without being told. If there are several, the caller
  names the one they want.
- `model_tier` is the **new** Phase 2 field: which model tier to use for detection —
  `economy`, `standard`, or `premium`. It's optional (falls back to the configured default),
  and an unknown value degrades to `standard` rather than erroring (D17).
- The brief's full API also lists `include_fix_suggestion` and `max_test_runtime_seconds`. We
  **left those out** on purpose (decision **D22**, the request-side of the honesty rule): we
  only advertise a knob once it actually does something. They'll be added in the phases that
  implement them. Pydantic simply ignores them if a client sends them early.

### `AnalyzeResponse` — what the caller gets back

```python
class AnalyzeResponse(BaseModel):
    analysis_id: str
    function: AnalyzedFunction
    properties: PropertyAnalysis
    strategy_plan: StrategyPlan
    test_file: GeneratedTestFile
```

The Phase 4 response is five things: a unique `analysis_id` (minted per request), the parsed
`function`, the `properties` it appears to satisfy, the `strategy_plan` (the Hypothesis strategies
for its inputs), and now the `test_file` (the complete, runnable pytest module). Each field is one
Phase *earned* by building the step behind it — `properties` in Phase 2, `strategy_plan` in Phase 3,
`test_file` in Phase 4 — appearing only because there's now real work to fill it (D5). `strategy_plan`
is named that, not `strategies`, so it doesn't read as `strategies.strategies` in the JSON (decision
**D30**); `test_file` joins it as the second field that "expose once real" rule has added (**D36**).
Still absent: `bugs_found`, `fix_suggestion`, `metadata` — those wait for the phases that make them
true.

## What could go wrong

### 1. Exposing internal data by accident
If the API returned `FunctionMetadata` directly instead of `AnalyzedFunction`, every response
would leak the raw function `source` and internal flags. Keeping a separate, smaller view
model makes that leak impossible — the API can only return fields the view defines.

### 2. The mutable-default trap
Writing `properties: list[...] = []` would make all objects secretly share one list.
`Field(default_factory=list)` is the correct fix and prevents a baffling "why did this other
object change?" bug. (The same trick guards `decorators`, `detected`, and `input_types`.)

### 3. Pretending features exist
Adding `bugs_found` or `fix_suggestion` to the response now — before any testing exists —
would make the API lie. Each field is added only in the phase that can fill it truthfully.
`properties` made the cut because the detection step is real; the others haven't yet (D5).

### 4. Letting the AI hand us things we already know (and could get wrong)
It would be tempting to have the AI return the function's types too. But we *know* the types
for certain from the AST — so asking the model for them only invites a hallucination. That's
why `PropertyDetection` is just the properties list, and `PropertyAnalysis` fills the types in
from the parser (D24). Ask the model only for the judgement call; keep the facts in code.

### 5. An out-of-range confidence
`confidence` is meant to be a probability-like 0–1 number. The `ge=0.0, le=1.0` constraint
makes Pydantic *enforce* that — a model reply with `confidence: 1.5` is rejected and Instructor
re-asks — so downstream code can trust the number without re-checking it.

### 6. Storing types as real objects instead of strings
If we tried to store the *actual* `int` type instead of the text `"int"`, we'd have to
evaluate the user's annotations — which can reference things we don't have, and is a security
and reliability risk. Keeping everything as descriptive strings sidesteps all of that.

## Summary

`models.py` is the data-shape rulebook for TypeWright, enforced by Pydantic. It draws a clean
line between the **rich internal** description of a function (`FunctionMetadata`, including its
source) and the **lean, honest API view** sent to callers. Phase 2's contribution is the
**property-class** family: `PropertyClass` (the closed menu of seven kinds), `DetectedProperty`
(one recognised property, with a testable `relation` and a 0–1 `confidence`), `PropertyDetection`
(the bare list the AI returns), and `PropertyAnalysis` (that list **plus** the AST-declared
types — the finished Phase 2 result that replaced the old `Contract`). The request gained a
`model_tier` knob and the response gained an honest `properties` field; everything still not
real yet (`bugs_found`, `fix_suggestion`) stays out. Phase 3 then adds the strategy models —
`GeneratedStrategy` (a Hypothesis strategy *expression* per argument, with a confidence) and
`StrategyPlan` (all of them + `extra_imports`) — the recipe Phase 4 turns into actual
tests. Phase 4 adds the two test-file boxes: `GeneratedTests` (the AI's raw test functions +
extra imports + a `skipped` list) and `GeneratedTestFile` (the finished, self-contained file:
`source`, `test_names` read off the parsed code, `skipped`). No logic lives here — just
well-labeled boxes for the rest of the program to fill.

## Change history

- **2026-06-09** — Created in Phase 1, Unit 3. Models: `ArgKind`, `Argument`,
  `FunctionMetadata` (internal), `AnalyzedFunction` (+`from_metadata`), `AnalyzeRequest`,
  `AnalyzeResponse`. Request/response intentionally omit later-phase fields (D5).
- **2026-06-12** — Phase 2 groundwork: added a `Contract` model
  (`preconditions` / `postconditions` / `invariants`) as the inferred-contract shape (§7.1,
  D14). Internal/working only; not yet exposed in `AnalyzeResponse`.
- **2026-06-13** — **Replaced `Contract` with the property-class models (D23/D24).** Added
  `PropertyClass` (enum of seven classes), `DetectedProperty` (`property_class`, testable
  `relation`, optional `companion_function`, `rationale`, range-checked `confidence`),
  `PropertyDetection` (the LLM's raw list), and `PropertyAnalysis` (detected + AST
  `input_types`/`return_type`). Added the intent-only `value_postcondition` class (D26).
  `AnalyzeRequest` gained `model_tier` (D22); `AnalyzeResponse` gained the now-honest
  `properties: PropertyAnalysis` field, replacing the planned `contract`.
- **2026-06-14** — Phase 3, Unit 1: added the strategy-generation output models (D29):
  `GeneratedStrategy` (`argument`, a `strategy` expression string, `rationale`, range-checked
  `confidence`) and `StrategyPlan` (`strategies` + `extra_imports`). Internal/working models —
  consumed by `generation.py` (unit 11) and not yet exposed in `AnalyzeResponse` (D28, still the
  D5 honesty rule).
- **2026-06-14** — Phase 3, Unit 2: `AnalyzeResponse` gained `strategy_plan: StrategyPlan` —
  the now-honest field earned by wiring generation into `/v1/analyze` (D30). Named
  `strategy_plan` (not `strategies`) to avoid a `strategies.strategies` nesting in the JSON.
- **2026-06-15** — Phase 4, Unit 1: added the test-generation models (D35). `GeneratedTests`
  (the AI's raw output: `test_functions`, `extra_imports`, `skipped`) and `GeneratedTestFile`
  (the finished artifact: `source`, `test_names` read off the parsed AST, `skipped`). Two models
  on purpose — the AI owns the tests, `testgen.py` (unit 12) owns the assembled file (D32).
  Internal/working models — consumed by `testgen.py`, not yet exposed in `AnalyzeResponse` (the
  D5 honesty rule; the `/v1/analyze` wiring + a `test_file` field come in Unit 2).
- **2026-06-15** — Phase 4, Unit 2: `AnalyzeResponse` gained `test_file: GeneratedTestFile` —
  the now-honest field earned by wiring test generation into `/v1/analyze` (D36). `test_file`
  joins `strategy_plan` as the second field the D5 "expose once real" rule has added to the
  response. (Also fixed: the top whole-file listing in this doc now includes the `GeneratedTests`
  / `GeneratedTestFile` models added in Unit 1.)
- **2026-06-19** — Phase 5, Unit 2: added the bug models (D40) — `BugSeverity` (`crash` vs
  `property_violation`), `Bug` (`test_name`, `failing_input`, `error`, `violated_property`,
  `severity`), and `BugReport` (the bugs list + `timed_out` / `exit_code` / `tests_passed` /
  `tests_failed` / `output_truncated`). These are the output of the result-parser (unit 15).
  Internal for now — `AnalyzeResponse` is untouched until Unit 3 surfaces `bugs` as `bugs_found`
  (the D5 honesty rule).
- **2026-06-19** — Phase 5, Unit 3 (API wiring, D41): `AnalyzeRequest` gained
  `max_test_runtime_seconds` (optional per-run sandbox budget, `gt=0`), and `AnalyzeResponse` gained
  `bugs_found: list[Bug]` — the now-honest field earned by wiring sandbox execution into
  `/v1/analyze`. `bugs_found` joins `strategy_plan` and `test_file` as the third field the D5 "expose
  once real" rule has added to the response (empty when every property held).
- **2026-06-25** — Phase 6: added the fix-suggestion models (D44, raw→artifact split like D35).
  `ProposedFix` (the AI's raw output: `corrected_source` + `explanation`) and `FixSuggestion` (the
  verified artifact: `code`, `verified`, `tests_passed`, `tests_failed`, plus the extra honest fields
  `explanation` and a fixed `disclaimer` carrying the brief's "AI suggestion — review carefully" label —
  allowed by D5, cf. D40). `AnalyzeRequest` gained `include_fix_suggestion: bool = False` (the field D22
  deferred — opt-in because the step adds an LLM call + a second sandbox run), and `AnalyzeResponse`
  gained `fix_suggestion: FixSuggestion | None` — the fourth field the D5 "expose once real" rule has
  added, three-state honest: `null` = not requested or no bugs; present + `verified=false` = "no
  confident fix"; present + `verified=true` = proven. Consumed by `fixgen.py` (unit 17).
- **2026-06-25** — Phase 7: added `PullRequestJob` (the minimal webhook → worker job — `repo_full_name`,
  `pr_number`, `head_sha`, `installation_id`; D47) and `FunctionFinding` (one changed function's result
  for the PR comment — `function_name`, `bugs`, optional `fix_suggestion`; D48). Consumed by
  `webhook.py`/`worker.py` (units 18/23) and `comment.py` (unit 21). No API-response change (these are
  internal to the GitHub-App path).
- **2026-06-28** — Phase 9 (Unit 1, D51): added `AnalysisMetadata` (`analysis_duration_ms`, `llm_cost_usd`,
  `tests_generated`, `tests_run`, `hypothesis_examples_tried: int | None`) and `AnalyzeResponse.metadata`
  (default-factory, so existing constructions don't break). This makes the §5 `metadata` block real — the
  field D5 parked until "the phase that makes it real." `hypothesis_examples_tried` is left null (not yet
  instrumented; honest-null over a faked 0, D5/D40). Built by `main.py`; cost summed via `metrics.py` (unit 26).
- **2026-06-28** — Phase 9 (Unit 2, D52): `AnalyzeRequest` gained `max_cost_usd: float | None` (gt=0) — the
  caller's per-analysis cost ceiling, which can only lower the server's configured cap. No response-shape
  change (enforcement raises `CostBudgetExceededError` → 402).
- **2026-06-28** — Phase 9 (Unit 5, D55): `AnalyzeRequest.code` gained `max_length=MAX_CODE_CHARS` (100,000,
  matching Kestrel's server cap), so an oversized payload is rejected up front by pydantic with **422** —
  before any LLM call or sandbox run.
- **2026-06-30** — Phase 10 (D60): new `BugVerdict` (a second-opinion verdict — `property_is_contractual` ∧
  `input_in_domain`, with `is_real` derived in code) and `Bug` gained `verification: BugVerdict | None = None`;
  `AnalyzeRequest` gained `verify_findings: bool | None` (per-request override). The field defaults to `None`,
  so existing serialization/tests are unchanged.
- **2026-06-30** — Phase 10 (D61): `FunctionMetadata` gained `module_imports` (the pasted code's module-level
  import lines, re-emitted by testgen so a top-level `import re` actually runs) and `imported_modules` (every
  top-level module name imported, for the dependency check). `AnalyzeResponse` gained `unavailable_imports` —
  packages the network-less sandbox can't provide (non-stdlib, non-allowlist); non-empty means the tests
  weren't executed here (no phantom crash), only generated. All three default to `[]`.

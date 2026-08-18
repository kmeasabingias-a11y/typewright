# 07 — `tests/` (conftest + parser + api + inference + generation tests)

## What this file is for

This unit is TypeWright's **safety net**.

Everything before now built the machine: the settings, the logger, the data
shapes, the error slips, the parser, the front desk, and the AI property-detection
step. The tests are what let us *trust* that machine — and, just as importantly,
keep trusting it as we keep changing it. Think of them like the test-drive a car
gets before it leaves the factory: turn the key (does it start?), press the brake
(does it stop?), try the turn signals (do they blink?). If any check fails, a light
goes on **before** the car reaches a customer.

A test is just a small program that *uses* our real code and then **asserts** —
states a fact that must be true. "If I parse `def add(a, b)`, the name must come
back as `add`." If the fact holds, the test passes silently. If it doesn't,
pytest stops and shows you exactly which fact broke. That's the whole game: every
test pins down one promise our code makes, so that if a future edit quietly breaks
that promise, we hear about it the same minute — not from a user weeks later.

Unit 7 is **five files**:

1. **`conftest.py`** — shared setup the other test files lean on (here: a test client for the
   web app whose **two AI steps are mocked**, so API tests never call a real model).
2. **`test_parser.py`** — tests for the parser, the brain of Phase 1.
3. **`test_api.py`** — tests for the HTTP front desk, driven like a real client.
4. **`test_inference.py`** — tests for the Phase 2 property-detection step (unit 09), with the
   AI swapped out for a fake.
5. **`test_generation.py`** — tests for the Phase 3 strategy-generation step (unit 11), the same
   way (AI faked). Added 2026-06-14; documented in its own section near the end.
6. **`test_testgen.py`** — tests for the Phase 4 test-file-generation step (unit 12), again with
   the AI faked. Added 2026-06-15; it also includes one test that actually *runs* the generated
   file (built from a test-authored fake, not the live AI) to prove it's collectable.

> **If you read the old version of this doc:** `test_inference.py` used to test
> *contract inference* (`infer_contract` → `Contract`). Phase 2 was redirected to
> property-class **detection** (decision **D23**), so that file now tests `infer_properties`
> → `PropertyAnalysis`. And because the API route now calls the AI step, `conftest.py` and
> `test_api.py` gained a way to **mock** that step — covered below. The suite grew from 27 to
> **30** passing tests.

---

## A mental model: a few ideas that make tests read easily

**1. Arrange → Act → Assert.** Almost every test has the same three beats. *Arrange*:
set up the input (a chunk of code to parse). *Act*: call the thing under test
(`parse_function(...)`). *Assert*: state what must be true about the result. Once you
see this rhythm, every test reads the same way.

**2. pytest finds tests by name.** We don't register tests anywhere. pytest scans the
`tests/` folder for files named `test_*.py` and, inside them, functions named
`test_*`. Each such function is one test. To run them all: `uv run pytest`.

**3. `assert` is the verdict.** Plain Python `assert something_true` is the heart of a
test. If the expression is true, nothing happens. If it's false, the test fails and
pytest prints a helpful comparison. For "this should *raise* an error" we use
`pytest.raises(...)`, which **passes only if** the expected error was thrown.

**4. A "fixture" is shared, reusable setup.** A test often needs the same starting
object — here, a client for talking to our web app. Rather than build it at the top of
every test, we write it once as a **fixture** in `conftest.py`. Any test that wants it
just names `client` as a parameter, and pytest hands it over. `conftest.py` is a magic
filename pytest loads automatically; you never import it yourself.

**5. The TestClient: a web client with no web.** To test the API we *could* start a
real server and use `curl`. Instead, FastAPI's **TestClient** lets us send requests to
the app **in-process** — no network, no ports, instant. `client.post("/v1/analyze",
json=...)` behaves like a real HTTP call but runs entirely inside the test.

**6. Mock the AI, two different ways.** The Phase 2 route makes a real AI call — but no
test should. So we *substitute* a fake for the model. There are two seams for this, one
per layer: the **API** tests swap the whole detection step via FastAPI's
`dependency_overrides` (the clean, framework-blessed way); the **inference** tests, which
test that step itself, swap the lower-level `_client()` with `monkeypatch`. Both end up
fast, free, and offline — they just cut the wire at different heights.

**7. Two styles of test, both here.** Most tests are **example-based**: *we* pick the
input and the expected output. One test is **property-based** (using **Hypothesis**):
instead of one example, we describe a *whole family* of valid inputs and a fact that
must hold for *all* of them; Hypothesis then invents dozens of examples trying to break
it. That second style is the seed of what TypeWright itself does for a living
(DECISIONS.md D11).

---

## The whole unit

### `tests/conftest.py`

```python
"""Shared pytest fixtures for the TypeWright test suite."""

import pytest
from fastapi.testclient import TestClient

from typewright.main import create_app, get_generate_strategies, get_infer_properties
from typewright.models import (
    DetectedProperty,
    GeneratedStrategy,
    PropertyAnalysis,
    PropertyClass,
    StrategyPlan,
)

# Fixed results the default fakes return, so API tests never hit a real LLM (both
# the detection and generation steps are injected via dependencies — D21, D28).
SAMPLE_ANALYSIS = PropertyAnalysis(
    detected=[
        DetectedProperty(
            property_class=PropertyClass.IDEMPOTENCE,
            relation="f(f(x)) == f(x)",
            rationale="normalizer — re-running changes nothing",
            confidence=0.9,
        )
    ],
    input_types={"x": "str"},
    return_type="str",
)

SAMPLE_PLAN = StrategyPlan(
    strategies=[
        GeneratedStrategy(
            argument="x",
            strategy="st.text()",
            rationale="any string is a valid input",
            confidence=0.9,
        )
    ],
    extra_imports=[],
)


def _default_infer(meta, *, model_tier=None) -> PropertyAnalysis:
    return SAMPLE_ANALYSIS


def _default_gen(meta, analysis, *, model_tier=None) -> StrategyPlan:
    return SAMPLE_PLAN


@pytest.fixture
def make_client():
    """Factory for a TestClient whose detection AND generation are mocked.

    Both LLM steps are injected via dependencies (D21, D28); overriding them lets
    API tests run with no live key. Defaults return ``SAMPLE_ANALYSIS`` /
    ``SAMPLE_PLAN``; pass a custom ``infer`` or ``gen`` to exercise tier selection
    or pipeline failures.
    """
    clients: list[TestClient] = []

    def _make(infer=_default_infer, gen=_default_gen) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_infer_properties] = lambda: infer
        app.dependency_overrides[get_generate_strategies] = lambda: gen
        client = TestClient(app)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.close()


@pytest.fixture
def client(make_client) -> TestClient:
    """A TestClient with both LLM steps mocked (SAMPLE_ANALYSIS + SAMPLE_PLAN).

    Building the app per test (via the ``create_app()`` factory) keeps each test
    isolated from the others' state.
    """
    return make_client()
```

### `tests/test_parser.py`

```python
"""Tests for the AST parser (``typewright.parser.parse_function``).

Example-based tests cover the Phase 1 exit criteria and the parser's chosen edge
cases (argument kinds, defaults, async, decorators, top-level-only scope, and the
four caller-facing errors). One Hypothesis test asserts the property that the
parser never crashes on valid single-function source (DECISIONS.md D11).
"""

import keyword

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from typewright.errors import (
    AmbiguousFunctionError,
    CodeSyntaxError,
    FunctionNotFoundError,
    NoFunctionError,
)
from typewright.models import ArgKind
from typewright.parser import parse_function


# --- The happy path: the Phase 1 exit criteria ------------------------------


def test_simple_function_metadata():
    source = (
        "def add(a: int, b: int) -> int:\n"
        '    """Add two numbers."""\n'
        "    return a + b\n"
    )
    meta = parse_function(source)

    assert meta.name == "add"
    assert meta.return_type == "int"
    assert meta.docstring == "Add two numbers."
    assert meta.is_async is False
    assert meta.decorators == []
    assert meta.signature == "(a: int, b: int) -> int"
    assert [arg.name for arg in meta.args] == ["a", "b"]
    assert all(arg.type_hint == "int" for arg in meta.args)
    assert "def add" in meta.source


def test_missing_annotations_and_docstring_are_none():
    meta = parse_function("def f(x):\n    return x")

    assert meta.return_type is None
    assert meta.docstring is None
    assert meta.args[0].type_hint is None
    assert meta.signature == "(x)"


# --- Argument kinds and defaults --------------------------------------------


def test_all_argument_kinds_in_order():
    meta = parse_function("def f(p, /, q, *args, r, **kwargs):\n    pass")

    assert [arg.name for arg in meta.args] == ["p", "q", "args", "r", "kwargs"]
    kinds = {arg.name: arg.kind for arg in meta.args}
    assert kinds["p"] == ArgKind.POSITIONAL_ONLY
    assert kinds["q"] == ArgKind.POSITIONAL_OR_KEYWORD
    assert kinds["args"] == ArgKind.VAR_POSITIONAL
    assert kinds["r"] == ArgKind.KEYWORD_ONLY
    assert kinds["kwargs"] == ArgKind.VAR_KEYWORD


def test_defaults_align_to_the_tail():
    meta = parse_function("def f(a, b=1, *, c, d=2):\n    pass")

    defaults = {arg.name: arg.default for arg in meta.args}
    assert defaults["a"] is None       # required positional
    assert defaults["b"] == "1"        # optional positional
    assert defaults["c"] is None       # required keyword-only
    assert defaults["d"] == "2"        # optional keyword-only


# --- async, decorators, and scope -------------------------------------------


def test_async_function_detected():
    meta = parse_function("async def fetch(url: str) -> str:\n    ...")

    assert meta.is_async is True
    assert meta.name == "fetch"


def test_decorators_captured_in_order():
    source = "@staticmethod\n@my.decorator\ndef handler():\n    pass\n"
    meta = parse_function(source)

    assert meta.decorators == ["staticmethod", "my.decorator"]


def test_only_top_level_functions_discovered():
    """Methods and nested functions are invisible (DECISIONS.md D7)."""
    source = (
        "class C:\n"
        "    def method(self):\n"
        "        pass\n"
        "\n"
        "def top():\n"
        "    def inner():\n"
        "        pass\n"
        "    return inner\n"
    )
    # `top` is the only *top-level* function, so no name is needed.
    meta = parse_function(source)
    assert meta.name == "top"


# --- Selecting among several functions --------------------------------------


def test_function_name_selects_among_many():
    source = "def a():\n    pass\n\ndef b(x: str) -> str:\n    return x"
    meta = parse_function(source, function_name="b")

    assert meta.name == "b"
    assert meta.return_type == "str"


# --- The four caller-facing errors ------------------------------------------


def test_syntax_error_raises_code_syntax_error():
    with pytest.raises(CodeSyntaxError):
        parse_function("def f(:\n    pass")


def test_no_function_raises_no_function_error():
    with pytest.raises(NoFunctionError):
        parse_function("x = 1\ny = 2")


def test_ambiguous_without_name_raises():
    with pytest.raises(AmbiguousFunctionError):
        parse_function("def a():\n    pass\n\ndef b():\n    pass")


def test_unknown_name_raises_function_not_found():
    with pytest.raises(FunctionNotFoundError):
        parse_function("def a():\n    pass", function_name="zzz")


# --- The one property-based test (DECISIONS.md D11) -------------------------

# Valid, non-keyword Python identifiers.
_identifiers = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]*", fullmatch=True).filter(
    lambda s: not keyword.iskeyword(s)
)


@settings(deadline=None)
@given(name=_identifiers, params=st.lists(_identifiers, unique=True, max_size=5))
def test_parser_never_crashes_on_valid_function(name, params):
    """For any valid single-function source, the parser returns clean metadata."""
    source = f"def {name}({', '.join(params)}):\n    pass\n"
    meta = parse_function(source)

    assert meta.name == name
    assert [arg.name for arg in meta.args] == params
```

### `tests/test_api.py`

```python
"""Tests for the HTTP surface (``POST /v1/analyze`` and ``/health``).

These drive the real FastAPI app through FastAPI's TestClient, asserting the
status-code contract (200 success, 400 caller error, 422 malformed request,
500 pipeline failure) and that the response carries only the honest Phase 3
subset: function + properties + strategy_plan (DECISIONS.md D5, D8, D21, D23, D30).
Both LLM steps are mocked via dependencies (conftest), so no live LLM key is needed.
"""

from typewright.errors import PipelineError
from typewright.models import PropertyAnalysis, StrategyPlan


def test_health_returns_ok(client):
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_analyze_returns_parsed_function(client):
    code = "def add(a: int, b: int) -> int:\n    return a + b"
    resp = client.post("/v1/analyze", json={"code": code})

    assert resp.status_code == 200
    body = resp.json()
    assert "analysis_id" in body
    fn = body["function"]
    assert fn["name"] == "add"
    assert fn["signature"] == "(a: int, b: int) -> int"
    assert fn["return_type"] == "int"
    assert [arg["name"] for arg in fn["args"]] == ["a", "b"]


def test_analyze_returns_only_the_honest_subset(client):
    """Phase 3 returns analysis_id + function + properties + strategy_plan — not bugs/fix yet."""
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})

    body = resp.json()
    assert set(body.keys()) == {"analysis_id", "function", "properties", "strategy_plan"}
    assert set(body["properties"].keys()) == {"detected", "input_types", "return_type"}
    assert set(body["strategy_plan"].keys()) == {"strategies", "extra_imports"}
    assert "bugs_found" not in body
    assert "fix_suggestion" not in body


def test_analyze_includes_detected_properties(client):
    """The mocked detection result is surfaced in the response."""
    resp = client.post(
        "/v1/analyze", json={"code": "def add(a, b):\n    return a + b"}
    )

    assert resp.status_code == 200
    properties = resp.json()["properties"]
    assert properties["detected"][0]["property_class"] == "idempotence"
    assert properties["detected"][0]["confidence"] == 0.9
    assert properties["return_type"] == "str"


def test_analyze_includes_generated_strategies(client):
    """The mocked strategy plan is surfaced in the response."""
    resp = client.post("/v1/analyze", json={"code": "def f(x):\n    return x"})

    assert resp.status_code == 200
    plan = resp.json()["strategy_plan"]
    assert plan["strategies"][0]["argument"] == "x"
    assert plan["strategies"][0]["strategy"] == "st.text()"


def test_model_tier_is_passed_to_inference(make_client):
    """The request's model_tier reaches the detection step verbatim."""
    seen = {}

    def infer(meta, *, model_tier=None):
        seen["tier"] = model_tier
        return PropertyAnalysis()

    client = make_client(infer)
    resp = client.post(
        "/v1/analyze",
        json={"code": "def f():\n    pass", "model_tier": "premium"},
    )

    assert resp.status_code == 200
    assert seen["tier"] == "premium"


def test_model_tier_is_passed_to_generation(make_client):
    """The request's model_tier reaches the generation step verbatim."""
    seen = {}

    def gen(meta, analysis, *, model_tier=None):
        seen["tier"] = model_tier
        return StrategyPlan()

    client = make_client(gen=gen)
    resp = client.post(
        "/v1/analyze", json={"code": "def f():\n    pass", "model_tier": "premium"}
    )

    assert resp.status_code == 200
    assert seen["tier"] == "premium"


def test_analyze_with_function_name(client):
    code = "def a():\n    pass\n\ndef b(x: str) -> str:\n    return x"
    resp = client.post("/v1/analyze", json={"code": code, "function_name": "b"})

    assert resp.status_code == 200
    assert resp.json()["function"]["name"] == "b"


def test_analysis_id_is_unique_per_call(client):
    code = "def f():\n    pass"
    first = client.post("/v1/analyze", json={"code": code}).json()["analysis_id"]
    second = client.post("/v1/analyze", json={"code": code}).json()["analysis_id"]

    assert first != second


# --- Caller errors map to 400 (the TypeWrightError family) ------------------


def test_syntax_error_is_400(client):
    resp = client.post("/v1/analyze", json={"code": "def f(:"})

    assert resp.status_code == 400
    assert "valid Python" in resp.json()["detail"]


def test_ambiguous_function_is_400(client):
    code = "def a():\n    pass\n\ndef b():\n    pass"
    resp = client.post("/v1/analyze", json={"code": code})

    assert resp.status_code == 400
    assert "function_name" in resp.json()["detail"]


def test_no_function_is_400(client):
    resp = client.post("/v1/analyze", json={"code": "x = 1"})

    assert resp.status_code == 400


def test_unknown_function_name_is_400(client):
    code = "def a():\n    pass"
    resp = client.post("/v1/analyze", json={"code": code, "function_name": "zzz"})

    assert resp.status_code == 400


# --- Malformed request shape is 422 (FastAPI validation, not our 400) -------


def test_missing_code_field_is_422(client):
    resp = client.post("/v1/analyze", json={})

    assert resp.status_code == 422


# --- Pipeline failure (our fault, not the caller's) is 500 with the stage -----


def test_pipeline_failure_is_500_with_stage(make_client):
    """A PipelineError from detection becomes a 500 naming the failing stage (D15)."""

    def infer(meta, *, model_tier=None):
        raise PipelineError("property_detection", "model unavailable")

    client = make_client(infer)
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})

    assert resp.status_code == 500
    body = resp.json()
    assert body["stage"] == "property_detection"
    assert "property_detection" in body["detail"]


def test_generation_failure_is_500_with_stage(make_client):
    """A PipelineError from generation becomes a 500 naming the failing stage (D15, D30)."""

    def gen(meta, analysis, *, model_tier=None):
        raise PipelineError("strategy_generation", "model unavailable")

    client = make_client(gen=gen)
    resp = client.post("/v1/analyze", json={"code": "def f():\n    pass"})

    assert resp.status_code == 500
    body = resp.json()
    assert body["stage"] == "strategy_generation"
    assert "strategy_generation" in body["detail"]
```

### `tests/test_inference.py` (Phase 2 — unit 09)

```python
"""Tests for property detection (Phase 2). The LLM is mocked — no live key needed (D20, D23)."""

from types import SimpleNamespace

import pytest

from typewright import inference
from typewright.config import Settings
from typewright.errors import PipelineError
from typewright.models import (
    Argument,
    DetectedProperty,
    FunctionMetadata,
    PropertyClass,
    PropertyDetection,
)


def _meta() -> FunctionMetadata:
    return FunctionMetadata(
        name="add",
        args=[
            Argument(name="a", type_hint="int"),
            Argument(name="b", type_hint="int"),
        ],
        return_type="int",
        signature="add(a: int, b: int) -> int",
        source="def add(a: int, b: int) -> int:\n    return a + b\n",
    )


class _FakeCompletions:
    """Stand-in for client.chat.completions: records kwargs, returns/raises on create()."""

    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.exc is not None:
            raise self.exc
        return self.result


def _fake_client(completions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _settings_with_key(monkeypatch, key="test-key"):
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    return Settings(_env_file=None)  # ignore any real .env so the test is hermetic


def _settings_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TYPEWRIGHT_ANTHROPIC_API_KEY", raising=False)
    return Settings(_env_file=None)


def test_infer_properties_returns_analysis(monkeypatch):
    detected = [
        DetectedProperty(
            property_class=PropertyClass.METAMORPHIC,
            relation="add(a, b) == add(b, a)",
            rationale="commutative",
            confidence=0.8,
        )
    ]
    fake = _FakeCompletions(result=PropertyDetection(properties=detected))
    monkeypatch.setattr(inference, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    result = inference.infer_properties(_meta(), settings)

    assert result.detected == detected
    assert result.input_types == {"a": "int", "b": "int"}  # from the AST, not the LLM
    assert result.return_type == "int"
    assert fake.kwargs["model"] == settings.model_standard  # default tier
    assert fake.kwargs["api_key"] == "test-key"
    assert fake.kwargs["response_model"] is PropertyDetection
    assert fake.kwargs["temperature"] == settings.llm_temperature


def test_infer_properties_uses_requested_tier(monkeypatch):
    fake = _FakeCompletions(result=PropertyDetection())
    monkeypatch.setattr(inference, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    inference.infer_properties(_meta(), settings, model_tier="premium")

    assert fake.kwargs["model"] == settings.model_premium


def test_infer_properties_missing_key_raises_pipeline_error(monkeypatch):
    settings = _settings_no_key(monkeypatch)
    with pytest.raises(PipelineError) as exc_info:
        inference.infer_properties(_meta(), settings)
    assert exc_info.value.stage == "property_detection"


def test_infer_properties_wraps_llm_failure(monkeypatch):
    fake = _FakeCompletions(exc=RuntimeError("boom"))
    monkeypatch.setattr(inference, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    with pytest.raises(PipelineError) as exc_info:
        inference.infer_properties(_meta(), settings)
    assert exc_info.value.stage == "property_detection"
    assert "boom" in exc_info.value.detail
```

---

## Step-by-step

### `conftest.py` — the shared, AI-mocked test client

In Phase 1 this fixture was a one-liner: `TestClient(create_app())`. Phase 2 made the
`/v1/analyze` route call a real AI model, so the fixture grew a way to **replace that call with
a fake** — and Phase 3 added a *second* AI step, so now it replaces **both**. It does that with
FastAPI's dependency-override mechanism.

**`SAMPLE_ANALYSIS` / `SAMPLE_PLAN` — canned answers.** Instead of asking a model, the default
fakes just return these fixed objects: `SAMPLE_ANALYSIS` (one detected property — idempotence,
confidence 0.9 — plus some types) for detection, and `SAMPLE_PLAN` (one strategy, `x:
st.text()`) for generation. They're the "pretend the AI said this" stand-ins, so tests can
assert on *known* results.

**`make_client` — a factory fixture.** This is the heart of the file. It's a fixture that
*returns a function* (`_make`) rather than a finished client, so each test can build a client
**and choose what the fake detector *and* generator do**:

```python
def _make(infer=_default_infer, gen=_default_gen) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_infer_properties] = lambda: infer
    app.dependency_overrides[get_generate_strategies] = lambda: gen
    client = TestClient(app)
    ...
```

The two key lines override the route's two `Depends(...)` dependencies. Recall from unit 06 that
the route asks for its detector via `Depends(get_infer_properties)` and its generator via
`Depends(get_generate_strategies)`. These lines tell the app: "whenever the route asks for those,
hand it *my* `infer` / `gen` instead." By default they're `_default_infer` / `_default_gen`
(returning `SAMPLE_ANALYSIS` / `SAMPLE_PLAN`), but a test can pass either its own — e.g. one that
records the tier it was called with, or one that *raises* a `PipelineError` to exercise the 500
path. Two seams, every AI behaviour a test could want, and never a real call.

The `clients` list plus the `yield … for client in clients: client.close()` is just tidy
housekeeping: every client the factory hands out gets closed after the test, so nothing leaks.

**`client` — the convenience fixture.** Most tests don't need to customise the fake; they just
want "a working client with the default canned answer." So `client` simply calls `make_client()`
with no arguments. Tests that need a custom fake ask for `make_client` instead; tests that
don't ask for `client`. Both build a **fresh app per test** (via `create_app()`), keeping every
test isolated from the others' state.

### `test_parser.py` — pinning down the brain (unchanged from Phase 1)

The parser tests are pure Phase 1 and didn't change in the redirect. A quick tour:

**The happy path (`test_simple_function_metadata`).** This *is* the Phase 1 exit criterion in
test form: feed in a normal annotated function with a docstring, and check every field we
promise comes back correct — name, return type, docstring, signature, argument names and
types, and that the original source is preserved.

**The "absent things are `None`" test.** A function with no annotations and no docstring must
give back `None` for those — not an empty string, not a crash.

**Argument kinds (`test_all_argument_kinds_in_order`).** One function using *every* one of
Python's five parameter kinds at once; the test checks each is labelled correctly **and that
the order is preserved**.

**Defaults align to the tail (`test_defaults_align_to_the_tail`).** Python stores defaults in a
list that lines up with the *end* of the parameters, so the parser does index arithmetic to
match each default to the right argument. This test proves that arithmetic: `a`/`c` required,
`b`/`d` carry their defaults.

**async, decorators, scope.** An `async def` is flagged `is_async=True`; stacked decorators are
captured in written order; and **only top-level functions are discovered** (DECISIONS.md D7) —
a class with a method and a function with a nested function yield exactly one visible thing,
`top`.

**Choosing among many, and the four error tests.** Naming `"b"` returns `b`. Then four
`pytest.raises(...)` tests pin the four ways input can be wrong — unparseable code, no function,
ambiguous, unknown name — the same four the API turns into `400`s.

**The property test (`test_parser_never_crashes_on_valid_function`).** The different one, and
the seed of TypeWright's reason for existing. `_identifiers` is a Hypothesis **strategy** — a
recipe for "any valid, non-keyword Python name." `@given(...)` runs the test many times,
inventing a fresh name and parameter list each time; inside, we build a guaranteed-valid
function and assert the parser echoes the name and lists those exact parameters — *whatever*
legal input we throw at it. `@settings(deadline=None)` removes Hypothesis's per-example time
limit so the test never flakes on a slow (e.g. WSL) machine. Examples check the cases *we
thought of*; a property check explores the ones we *didn't* (DECISIONS.md D11).

### `test_api.py` — driving the front desk like a real client

These tests go through the *front door* — the HTTP endpoints — exactly as `curl` or the future
web demo would. With both AI steps mocked in `conftest.py`, they're **end-to-end** for Phase 3:
request shape → routing → parser → detection (faked) → generation (faked) → error mapping → JSON
response.

- **`test_health_returns_ok`** — the doorbell answers `200` and `{"status": "ok"}`.
- **`test_analyze_returns_parsed_function`** — POST a function, get `200` with the right
  `name`, `signature`, `return_type`, arg names, and an `analysis_id`. The parser half still
  works exactly as in Phase 1.
- **`test_analyze_returns_only_the_honest_subset`** — a guard on a *decision*, updated for
  Phase 3. The response keys must now be **exactly** `{analysis_id, function, properties,
  strategy_plan}` — `strategy_plan` is in (it's real now), but `bugs_found` and `fix_suggestion`
  must still be **out**. It checks the inner shape of both `properties` and `strategy_plan`. If a
  future phase leaks a half-built field into the response, this fails loudly (DECISIONS.md D5).
- **`test_analyze_includes_detected_properties`** — proves the detector's result reaches the
  response: the canned `SAMPLE_ANALYSIS` (idempotence, confidence 0.9, return type `str`) shows
  up in the JSON.
- **`test_analyze_includes_generated_strategies`** — the Phase 3 twin: proves the *generator's*
  result reaches the response too — the canned `SAMPLE_PLAN` (`x: st.text()`) shows up under
  `strategy_plan`. Together these two are the link from "both AI steps ran" to "the caller sees
  it."
- **`test_model_tier_is_passed_to_inference`** / **`test_model_tier_is_passed_to_generation`** —
  each uses `make_client` with a *custom* fake that records the `model_tier` it was handed.
  Sending `"model_tier": "premium"` must reach **both** the detector and the generator with
  `model_tier="premium"` — proving the request field is wired all the way through to each AI
  step, not silently dropped.
- **`test_analyze_with_function_name`** and **`test_analysis_id_is_unique_per_call`** — naming a
  function picks the right one; two identical requests get two different ids.
- **The four `..._is_400` tests** — each bad input the parser rejects must surface as `400`,
  proving the single `TypeWrightError`-to-`400` handler catches the whole family.
- **`test_missing_code_field_is_422`** — a request with no `code` field is a *different* wrong:
  the request didn't have the required shape, so FastAPI returns `422` before our code runs.
- **`test_pipeline_failure_is_500_with_stage`** / **`test_generation_failure_is_500_with_stage`**
  — the our-fault tests, one per AI step. Each uses `make_client` with a fake that *raises*
  `PipelineError` for its stage (`"property_detection"` / `"strategy_generation"`), then asserts
  the response is `500` with that `stage` in the body. Together they prove the unit-06 handler
  works for *either* stage and pin the contract that a 500 names the failing stage (D15, D30).

Together these lock in the full status-code contract — **200 / 400 / 422 / 500** — and the
honest Phase 3 response shape.

### `test_inference.py` — testing the AI step without any AI (Phase 2)

This file tests `infer_properties` (unit 09), and its defining trick is the same as before:
**there is no real AI call.** A live model would make the suite slow, cost money, demand a
secret in CI, and flake whenever the provider hiccupped. So every test swaps the model out for
a fake (decision **D20**). The difference from the API tests is the *height* of the cut: here we
replace the low-level `inference._client`, because we're testing the detection logic itself.

**The swap (`monkeypatch.setattr`).** `monkeypatch` is a pytest fixture that changes something
*just for one test*, then restores it. The key line:

```python
monkeypatch.setattr(inference, "_client", lambda: _fake_client(fake))
```

replaces `inference._client` — the little function whose only job is to build the real AI
client — with one that returns our fake. That single seam is the whole reason `_client()` was
pulled out into its own function (unit 09). After this line, `infer_properties` runs its real
logic but talks to the fake.

**The fake itself (`_FakeCompletions` + `_fake_client`).** A real Instructor client is called as
`client.chat.completions.create(...)`. `_fake_client` mimics that exact path with nested
`SimpleNamespace` objects so `.chat.completions` leads to our `_FakeCompletions`. That class
does two jobs: it **records** the keyword arguments it was called with (`self.kwargs = kwargs`)
so a test can inspect *how* the model was asked, and it either **returns** a known
`PropertyDetection` or **raises** an exception we chose — one fake covering both success and
failure.

**Hermetic settings (`_settings_with_key` / `_settings_no_key`).** These build a `Settings` that
ignores any real `.env` via `Settings(_env_file=None)`, so the result never depends on the
developer's machine. `_settings_with_key` sets `ANTHROPIC_API_KEY` first; `_settings_no_key`
deletes *both* accepted names. This also quietly confirms the key alias from unit 01 works —
the plain `ANTHROPIC_API_KEY` (no `TYPEWRIGHT_` prefix) is picked up.

**The `_meta()` helper** builds a small `add(a, b)` `FunctionMetadata` with real `Argument`s —
note it now carries typed args, because one test checks that the *types in the result* come from
this AST metadata, not the model.

Now the four tests, each pinning one promise:

- **`test_infer_properties_returns_analysis`** — the happy path. With a key present and the fake
  returning a known `PropertyDetection` (one metamorphic property), `infer_properties` returns a
  `PropertyAnalysis` whose `detected` is that list — **and** whose `input_types` /`return_type`
  come straight from the AST (`{"a": "int", "b": "int"}`, `"int"`), *not* the model. That's the
  D24 split, tested directly. It then peeks at the recorded `kwargs` to prove the call was built
  correctly: the **standard** tier by default, the API key forwarded, `response_model` is
  `PropertyDetection`, and `temperature` equals the configured `llm_temperature` (0.0, D25). It
  checks not just the *result* but *how the model was asked*.
- **`test_infer_properties_uses_requested_tier`** — pass `model_tier="premium"` and the recorded
  `model` must be the premium model. Proves the tier-to-model mapping is actually wired through.
- **`test_infer_properties_missing_key_raises_pipeline_error`** — with no key, `infer_properties`
  stops early and raises a `PipelineError` whose `stage` is `"property_detection"`. (No fake
  client here at all — the guard fires *before* any call.)
- **`test_infer_properties_wraps_llm_failure`** — the fake raises `RuntimeError("boom")`, standing
  in for the network dropping or the provider erroring. The test confirms that raw failure is
  caught and re-dressed as a `PipelineError` for the right stage, *and* that the original reason
  survives in `.detail` — so debugging info isn't lost on the way to the 500.

Together these cover both directions of unit 09's contract: on success it returns a real
`PropertyAnalysis` (model's properties + AST's types) and calls the model correctly; on failure
(no key, or the call blows up) it funnels everything into one honest `PipelineError` — all
without a network or a paid key.

### `test_generation.py` — the same trick, one phase later (Phase 3)

This file tests `generate_strategies` (unit 11), and it is a near-twin of `test_inference.py` on
purpose — same fake, same `monkeypatch.setattr` seam, same hermetic settings helpers. Rather than
re-explain the machinery, here's the file, then just what *differs*:

```python
"""Tests for strategy generation (Phase 3). The LLM is mocked — no live key needed (D28)."""

from types import SimpleNamespace

import pytest

from typewright import generation
from typewright.config import Settings
from typewright.errors import PipelineError
from typewright.models import (
    Argument,
    FunctionMetadata,
    GeneratedStrategy,
    PropertyAnalysis,
    StrategyPlan,
)


def _meta() -> FunctionMetadata:
    return FunctionMetadata(
        name="add",
        args=[Argument(name="a", type_hint="int"), Argument(name="b", type_hint="int")],
        return_type="int",
        signature="add(a: int, b: int) -> int",
        source="def add(a: int, b: int) -> int:\n    return a + b\n",
    )


def _analysis() -> PropertyAnalysis:
    return PropertyAnalysis(detected=[], input_types={"a": "int", "b": "int"}, return_type="int")


# ... _FakeCompletions, _fake_client, _settings_with_key, _settings_no_key:
#     identical to test_inference.py ...


def test_generate_strategies_returns_plan(monkeypatch):
    plan = StrategyPlan(strategies=[
        GeneratedStrategy(argument="a", strategy="st.integers()", rationale="int", confidence=0.95),
        GeneratedStrategy(argument="b", strategy="st.integers()", rationale="int", confidence=0.95),
    ])
    fake = _FakeCompletions(result=plan)
    monkeypatch.setattr(generation, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)

    result = generation.generate_strategies(_meta(), _analysis(), settings)

    assert result is plan                                  # returned directly, no bolt-on
    assert [s.argument for s in result.strategies] == ["a", "b"]
    assert fake.kwargs["model"] == settings.model_standard
    assert fake.kwargs["api_key"] == "test-key"
    assert fake.kwargs["response_model"] is StrategyPlan
    assert fake.kwargs["temperature"] == settings.llm_temperature


def test_generate_strategies_uses_requested_tier(monkeypatch):
    fake = _FakeCompletions(result=StrategyPlan())
    monkeypatch.setattr(generation, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)
    generation.generate_strategies(_meta(), _analysis(), settings, model_tier="premium")
    assert fake.kwargs["model"] == settings.model_premium


def test_generate_strategies_missing_key_raises_pipeline_error(monkeypatch):
    settings = _settings_no_key(monkeypatch)
    with pytest.raises(PipelineError) as exc_info:
        generation.generate_strategies(_meta(), _analysis(), settings)
    assert exc_info.value.stage == "strategy_generation"


def test_generate_strategies_wraps_llm_failure(monkeypatch):
    fake = _FakeCompletions(exc=RuntimeError("boom"))
    monkeypatch.setattr(generation, "_client", lambda: _fake_client(fake))
    settings = _settings_with_key(monkeypatch)
    with pytest.raises(PipelineError) as exc_info:
        generation.generate_strategies(_meta(), _analysis(), settings)
    assert exc_info.value.stage == "strategy_generation"
    assert "boom" in exc_info.value.detail
```

What's different from the inference tests, and why each difference matters:

- **It swaps `generation._client`, not `inference._client`.** Each AI module keeps its *own*
  `_client()` seam (over the shared `build_client` in unit 10), so each test file patches its own
  module. This is the payoff of keeping per-module seams during the unit-10 refactor.
- **It feeds two inputs:** a `_meta()` *and* a `_analysis()` — because `generate_strategies(meta,
  analysis)` needs both the parsed function and the Phase 2 result.
- **`assert result is plan` (identity, not equality).** Unit 11 returns the model's `StrategyPlan`
  **directly** — no AST bolt-on like `infer_properties` does — so the exact object comes back.
  (Compare `test_infer_properties_returns_analysis`, which uses `==` because a *new*
  `PropertyAnalysis` is assembled.) This one assertion pins decision **D29**.
- **`response_model` is `StrategyPlan`** and the stage is **`"strategy_generation"`** — the Phase
  3 equivalents of `PropertyDetection` / `"property_detection"`.

Everything else — the `temperature` forwarding (D25), tier selection, the missing-key guard, and
failure-wrapping with the reason preserved in `.detail` — is checked exactly as for detection,
because the two steps share the same shape.

### `test_kestrel.py` & `test_execution.py` — testing the sandbox without a sandbox (Phase 5)

Phase 5 adds code that makes a real HTTP call to the Kestrel service. The tests must check it
**without** a live Kestrel running anywhere — the same "test the step without the thing it depends
on" trick we used for the AI, just with a different kind of stand-in.

For the AI we monkeypatched a `_client()` that returned a fake. For HTTP, `httpx` gives us a purpose-
built tool: **`httpx.MockTransport`**. You hand it a small `handler(request)` function; instead of
the request going out over the network, `httpx` calls your handler and uses whatever `httpx.Response`
it returns. So we can pretend to *be* Kestrel for the length of a test.

`test_kestrel.py` (6 tests) monkeypatches `kestrel._client` to return a client wired to a
`MockTransport`, and checks the things that actually matter about the call:

- **It posts the right thing.** The handler captures the request and we assert the path is
  `/execute` and the JSON body is exactly `{"code": ..., "timeout_seconds": ...}`.
- **The auth header is right.** With a key set, the client sends `Authorization: Bearer <key>`;
  with no key, **no** such header — proving the auth-off-locally path.
- **A timeout is data, not an error.** The handler returns a normal `200` with `timed_out: true`;
  the test asserts `run_in_sandbox` returns a `SandboxResult(timed_out=True)` and does **not** raise.
- **A real HTTP failure raises.** The handler returns a `500`; the test asserts a `PipelineError`
  with stage `"sandbox_execution"` comes out. (This is the line between "the code we ran misbehaved"
  and "the phone call itself failed", from unit 13's mental model.)
- **The HTTP timeout is the run budget plus the buffer.** A captured-timeout test confirms a 30s
  budget produces a 45s HTTP read timeout (with a 15s buffer) — the margin that stops us hanging up
  on a still-running sandbox.

`test_execution.py` (3 tests) needs no transport at all, because it's mostly about *building the
right string*:

- `wrap_for_sandbox` output is **valid Python** (checked with `ast.parse`) and contains the three
  preamble markers (`os.chdir("/tmp")`, `database=None`, `deadline=None`) and the `pytest.main`
  runner.
- The wrap **preserves the original file** (the function and test names are still in there).
- `run_tests` **wraps then submits**: it monkeypatches `execution.run_in_sandbox` to capture what
  was sent, and asserts the submitted code equals `wrap_for_sandbox(test_file.source)` and the time
  budget was passed straight through.

### `test_results.py` — reading bug reports out of fake pytest output (Phase 5)

The result-parser (unit 15) is pure text-reading with no AI and no network, so its tests are the
most self-contained in the suite: each one hands `parse_results` a hand-written string that *looks
like* real pytest/Hypothesis output and checks the `BugReport` that comes back. No sandbox, no
mocking — just "given this transcript, find these bugs". 10 tests cover the cases that matter:

- **All passed** → no bugs, and the passed-count is read from the tally line.
- **A bare-assert failure** (`FAILED main.py::test_idempotence - assert 'AA' == 'A'`) →
  `severity=property_violation`, `error="AssertionError"`, and the multi-line `Falsifying example`
  is normalised to `failing_input="x='A'"`. This pins the tricky bit: pytest prints bare assertions
  *without* an `AssertionError:` label, and the parser still classifies them correctly.
- **A crash** (`- IndexError: ...`) → `severity=crash`, `error="IndexError"`.
- **An `AssertionError:`-with-message failure** → still `property_violation` (the other spelling).
- **Two failures of the same property class** (`test_metamorphic`, `test_metamorphic_2`) → each maps
  to the *n-th* detected relation, proving the `_2` suffix logic.
- **A timeout** → no bugs but `timed_out=True` (the 504 signal).
- **Truncated output** → `output_truncated=True`.
- **A value containing brackets** (`test_f(s=')(')`) → the balanced-scan still captures `s=')('`.
- **The safety net** → a failing run with a falsifying example but no summary line still yields a bug.

Because there's no live anything, these tests are fast and deterministic — and they double as
executable documentation of exactly what pytest output the parser expects.

### Running them

```sh
uv run pytest          # run everything
uv run pytest -q       # quieter output
uv run pytest tests/test_inference.py::test_infer_properties_returns_analysis  # one test
```

`pyproject.toml` already points pytest at the `tests/` folder and puts `src/` on the import
path, so these just work after `uv sync`.

---

## What could go wrong

### 1. Tests that quietly test nothing
A test with no `assert` (or one that asserts something always true) passes forever while
checking *nothing* — worse than no test, because it looks like coverage. Every test here ends
in a concrete assertion about a specific value.

### 2. Tests that leak into each other
If all tests shared one app instance, one test mutating state (including its
`dependency_overrides`!) could change another's result — the maddening "passes alone, fails
together." Building a fresh `create_app()` per test, and closing each client afterward, keeps
every test hermetic.

### 3. An API test that secretly calls a real model
If the route's detector weren't overridden, every API test would need a live key and a network,
and would flake on provider hiccups. The `dependency_overrides[get_infer_properties]` seam
guarantees the AI is faked — and the one test that *wants* a failure (`..._is_500_with_stage`)
gets it deterministically by having the fake raise, not by hoping the provider errors.

### 4. Confusing "raises an error" with "fails"
For the error cases, a *passing* test requires the error to be **raised**. `pytest.raises(...)`
inverts the usual logic: if the code *didn't* raise, the test fails. Writing these as
`try/except` by hand is easy to get backwards (a bare `except` can swallow the very failure
you're testing for).

### 5. A property test that secretly tests one shape
The point of Hypothesis is *variety*. If the strategy were too narrow, it would dress up an
example test as a property test and explore nothing. Generating both the name and a
variable-length, unique parameter list keeps the input genuinely diverse — including the
empty-parameter and single-parameter edges.

### 6. Trusting the model for facts we already have
`test_infer_properties_returns_analysis` deliberately checks that `input_types`/`return_type`
come from the AST, not the model's reply. If a future refactor let the model supply the types,
this test would catch the regression — those types must stay sourced from the parser (D24), or
a hallucinated type could slip into the result.

### 7. Asserting on brittle text
The error-message asserts check for a stable *substring* (`"valid Python"`, `"function_name"`,
`"property_detection"`) rather than a whole sentence, so rewording a message doesn't break a
test for no real reason.

---

## Summary

Unit 7 is the safety net that lets us trust — and keep changing — everything built so far.
`conftest.py` provides a fresh, isolated `TestClient` per test **with both AI steps mocked** via
FastAPI's `dependency_overrides` (a `make_client` factory taking custom `infer`/`gen` fakes, a
`client` convenience for the defaults). `test_parser.py` (unchanged) pins the parser's promises,
including one Hypothesis property test. `test_api.py` drives the real app through HTTP, locking
in the full status-code contract — **200 / 400 / 422 / 500** — the honest
`{analysis_id, function, properties, strategy_plan}` response shape, that detected properties,
generated strategies, and the `model_tier` all reach the right places, and that a failure in
*either* AI stage becomes a 500 naming that stage. `test_inference.py` and `test_generation.py`
test the two AI steps offline by swapping each module's `_client` for a fake — each covering a
clean success and both failure paths (no key, call blows up), all without a network or a paid
key (D20, D23, D28). Run them all with `uv run pytest`; the suite is green at **37** (30 from
Phases 1–2, plus 4 generation-unit tests and 3 API-wiring tests).

---

## Change history

- **2026-06-11** — Created in Phase 1, Unit 7. `conftest.py` `client` fixture (fresh
  `create_app()` per test); `test_parser.py` example tests for the exit criterion, argument
  kinds, defaults, async/decorators, top-level-only scope (D7), and the four domain errors,
  plus one Hypothesis "never crashes on valid source" property test (D11); `test_api.py`
  end-to-end tests for `/health`, `/v1/analyze`, the 200/400/422 contract, and the honest
  `{analysis_id, function}` response (D5, D8). Verified: 23 passed.
- **2026-06-12** — Phase 2, Unit 2: added `test_inference.py` (4 tests) for `infer_contract`,
  mocking the LLM by monkeypatching `inference._client` and using `Settings(_env_file=None)`
  (D20). Suite 27 passed.
- **2026-06-13** — **Phase 2 redirect (D23) + API wiring (D21).** `test_inference.py` rewritten
  for `infer_properties` → `PropertyAnalysis`: the happy path now asserts the model supplies
  `detected` while the **AST** supplies `input_types`/`return_type` (D24) and that `temperature`
  is forwarded (D25); the stage name in the two failure tests is now `"property_detection"`.
  `conftest.py` gained a `make_client` factory and `SAMPLE_ANALYSIS`, mocking the route's AI
  step via `app.dependency_overrides[get_infer_properties]` (D21). `test_api.py` updated for the
  `{analysis_id, function, properties}` shape and gained three tests:
  `test_analyze_includes_detected_properties`, `test_model_tier_is_passed_to_inference`, and
  `test_pipeline_failure_is_500_with_stage` (the 500-with-stage path, D15). Suite now **30
  passed**.
- **2026-06-14** — Phase 3, Unit 1: added `test_generation.py` (4 tests) for `generate_strategies`
  (unit 11), a near-twin of `test_inference.py` — it monkeypatches `generation._client`, feeds a
  `_meta()` + `_analysis()`, and asserts the model's `StrategyPlan` is returned **directly**
  (`result is plan`, D29) with `response_model=StrategyPlan` and stage `"strategy_generation"`.
  No change to the other test files. Suite now **34 passed**.
- **2026-06-14** — Phase 3, Unit 2 (API wiring, D30): `conftest.py` now mocks **both** AI steps —
  added `SAMPLE_PLAN`, `_default_gen`, a `gen` param on `make_client`, and the
  `get_generate_strategies` override. `test_api.py`'s honest-subset test now expects
  `{analysis_id, function, properties, strategy_plan}`, and gained three tests:
  `test_analyze_includes_generated_strategies`, `test_model_tier_is_passed_to_generation`, and
  `test_generation_failure_is_500_with_stage`. Suite now **37 passed**.
- **2026-06-15** — Phase 4, Unit 1: added `test_testgen.py` (8 tests) for `generate_test_file`
  (unit 12), built on the same faked-AI pattern (monkeypatch `testgen._client`, feed a `_meta()`
  + `_analysis()` + `_plan()`). It asserts the assembled file is self-contained (the function
  source is prepended), that `test_names` are read off the AST, that strategy/test `extra_imports`
  are merged + deduped, that `skipped` passes through, that tier selection works, and that a
  non-parsing file and a missing key and an LLM failure all raise `PipelineError` for stage
  `"test_generation"`. One test (`test_generated_file_is_collectable_and_passes`) `exec`s a file
  assembled from a *test-authored* `GeneratedTests` (not the live AI) and runs the `@given` test
  to prove the output is genuinely runnable — the exit criterion in miniature. The refactor that
  routed inference/generation through `llm.complete` (D31) left `test_inference.py` and
  `test_generation.py` unchanged and green. No `/v1/analyze` wiring yet, so `conftest.py`/
  `test_api.py` are untouched (that comes in Unit 2). Suite now **45 passed**.
- **2026-06-15** — Phase 4, Unit 2 (API wiring, D36): `conftest.py` now mocks **all three** AI
  steps — added `SAMPLE_TEST_FILE`, `_default_testgen`, a `gen_tests` param on `make_client`, and
  the `get_generate_test_file` override. `test_api.py`'s honest-subset test now expects
  `{analysis_id, function, properties, strategy_plan, test_file}`, and gained three tests:
  `test_analyze_includes_test_file`, `test_model_tier_is_passed_to_testgen`, and
  `test_testgen_failure_is_500_with_stage` (the all-or-nothing 500-with-stage path). Suite now
  **48 passed**.
- **2026-06-19** — Phase 5, Unit 1: added `test_kestrel.py` (6 tests) and `test_execution.py`
  (3 tests) — the standalone sandbox capability, tested with **no live Kestrel**. `test_kestrel.py`
  monkeypatches `kestrel._client` to use an `httpx.MockTransport` and checks the `/execute` request
  shape, the `Bearer` auth header (present with a key, absent without), "a timeout is data not an
  error" (`200` + `timed_out:true` → `SandboxResult`, no raise), an HTTP `500` → `PipelineError(stage=
  "sandbox_execution")`, and the HTTP-timeout = budget + buffer math. `test_execution.py` checks
  `wrap_for_sandbox` is valid Python with the preamble + `__main__` runner and preserves the original
  file, and that `run_tests` wraps-then-submits with the budget threaded through. No `/v1/analyze`
  change yet (standalone-first), so `conftest.py`/`test_api.py` are untouched. Suite now **57 passed**.
- **2026-06-19** — Phase 5, Unit 2: added `test_results.py` (10 tests) for `parse_results` — pure
  text-reading, no sandbox/LLM. Each test feeds a hand-written pytest/Hypothesis transcript and asserts
  the resulting `BugReport`: pass → no bugs; bare-assert → `property_violation`/`AssertionError`;
  crash → `crash`/`IndexError`; repeated class → n-th relation; timeout → `timed_out`; truncation;
  brackets-in-value; and the no-summary safety net. No `/v1/analyze` change yet. Suite now **67 passed**.
- **2026-06-19** — Phase 5, Unit 3 (API wiring, D41/D42): `conftest.py` now mocks **all four** pipeline
  steps — added `SAMPLE_SANDBOX_RESULT` (a clean "1 passed" run), `_default_run`, a `run` param on
  `make_client`, and the `get_run_tests` override. The seam is the I/O boundary only (D41): tests mock
  the sandbox call but the route runs the *real* `parse_results`, so a failing `SandboxResult` flows
  through to real `bugs_found`. `test_api.py`'s honest-subset test now expects `bugs_found`, and gained
  five tests: `test_analyze_includes_bugs_found` (empty on clean run), `test_bugs_found_surfaces_sandbox_failures`
  (a crafted FAILED/Falsifying `SandboxResult` → a parsed bug with the mapped relation),
  `test_max_test_runtime_seconds_passed_through`, `test_timeout_is_504` (D42), and
  `test_sandbox_failure_is_500_with_stage`. Suite now **72 passed**.
- **2026-06-25** — Phase 6, Unit 1: added `test_fixgen.py` (13 tests) for the fix-suggestion module,
  AI mocked (no live key). `suggest_fix` tests check it returns the `ProposedFix`, threads the model
  tier, sends the failing input + violated relation into the prompt, and wraps a missing key / LLM
  failure as `PipelineError(stage="fix_suggestion")`. `build_fix_file` tests check the happy swap
  (corrected body in, buggy body out, tests + imports kept) — including an **exec-and-run** that proves
  the spliced file actually passes the same `@given` test — and the three degrade-to-`None` guards
  (corrected unparseable / wrong function name / original absent). `finalize` tests cover the verdict
  matrix: green re-run → `verified=True`; still-failing / `None` report / timed-out → `verified=False`,
  with the counts and disclaimer carried. Suite **86 passed**.
- **2026-06-25** — Phase 6, Unit 2 (API wiring, D44/D45): `conftest.py` now mocks **all five** steps —
  added `SAMPLE_PROPOSED_FIX`, `_default_suggest`, a `suggest` param on `make_client`, and the
  `get_suggest_fix` override. `test_api.py`'s honest-subset test now expects `fix_suggestion` (null when
  not requested), and gained six tests: `test_fix_suggestion_absent_by_default` (opt-in),
  `test_fix_suggestion_skipped_when_no_bugs` (requested but clean run → `suggest` never called),
  `test_fix_suggestion_verified_when_rerun_green` (a stateful `run` that fails then passes → `verified=True`,
  and asserts the run was called **twice** — once to find bugs, once to verify),
  `test_fix_suggestion_unverified_when_rerun_still_fails`, `test_fix_generation_failure_degrades_not_500`
  (the best-effort break from all-or-nothing — a fix-gen `PipelineError` → 200 with `bugs_found` intact +
  `fix_suggestion: null`), and `test_model_tier_is_passed_to_fix`. Suite now **92 passed**.
- **2026-06-25** — Phase 7: six new test files, all mocked (no GitHub / Redis / network). `test_webhook.py`
  (signature verify + event parse as pure functions, plus the route via `dependency_overrides` with a
  capturing fake enqueue: 202 queued / 403 bad-sig / 200 ignored / skip-when-no-secret), `test_github.py`
  (the App client via `httpx.MockTransport`, `_app_jwt` monkeypatched: token, paginated files, raw file
  content, comment, + error paths), `test_diff.py` (unified-diff line parsing + function selection, pure),
  `test_comment.py` (the markdown formatter, pure), `test_analysis.py` (`analyze_one` with the pipeline
  steps mocked), `test_worker.py` (`process_pr` with GitHub + `analyze_one` mocked: comments on bugs,
  skips non-`.py`/removed, no comment when clean, skips a function on error). Suite **134 passed**.
- **2026-06-28** — Phase 8 (web demo, unit 24): new `test_web.py` (2 tests, no mocks needed — `GET /` has
  no pipeline dependencies). `test_index_is_served_as_html` (200 + `text/html` content-type) and
  `test_index_wires_to_the_real_endpoint` (the page contains `/v1/analyze`, `include_fix_suggestion`, and
  the `#code` / `#analyze` controls — so a rename can't silently unwire the demo). Suite **136 passed**.
- **2026-06-28** — Phase 8 (Unit 2a, store, unit 25): new `test_store.py` (3 tests on a real `SqliteRunStore`
  pointed at pytest's `tmp_path` — save→load round-trip, unknown id → `None`, idempotent overwrite +
  durability across a second store instance on the same file) + 3 new `test_api.py` tests (analyze then
  `GET /v1/runs/{id}` returns the same body; unknown id → 404; a `save` that raises still returns 200 —
  best-effort). `conftest.py` gained a `get_run_store` override → a fresh `InMemoryRunStore` per client
  (so the suite never writes a `runs.db`). Suite **142 passed**.
- **2026-06-28** — Phase 8 (Unit 2b, share/view): `test_web.py` gained a third test
  (`test_index_supports_shared_links` — the page contains `/v1/runs/` and an `id="share"` bar, so the
  shared-link wiring can't silently regress). Suite **143 passed**.
- **2026-06-28** — Phase 9 (Unit 1, D51): new `test_metrics.py` (4 tests — `CostMeter` accumulates;
  `add_cost` within a `cost_scope` bills the meter; `add_cost` outside a scope is a no-op; `_response_cost`
  degrades to 0.0 on bad input) + an API test (`test_analyze_includes_metadata` — `metadata` present with
  real `tests_generated`/`tests_run`, `llm_cost_usd == 0.0` on the mocked path, `hypothesis_examples_tried`
  null). The honest-subset test gained `metadata` to its key set. Suite **148 passed**.
- **2026-06-28** — Phase 9 (Unit 2, D52): `test_metrics.py` +2 (a budget-capped `CostMeter` raises
  `CostBudgetExceededError` when the total crosses the limit; same via `cost_scope(limit)` + `add_cost`) and
  `test_api.py` +2 (a step raising the error → **402** with `spent_usd`/`limit_usd` in the body; a request
  `max_cost_usd` lower than what a step spends → 402, proving the route's `cost_scope` + clamp). Suite **152 passed**.
- **2026-06-28** — Phase 9 (Unit 3, D53): new `test_ratelimit.py` (4 — `InMemoryRateLimiter` allows up to the
  limit then blocks; separate keys are independent; `RedisRateLimiter` counts via a fake redis client; and
  fails open when the client raises) + a `test_api.py` 429 test (an injected blocking limiter → 429 with the
  `Retry-After` header and `retry_after` body). The limiter lives on `app.state`, so existing tests need no
  change (each app gets its own, well under the default limits). Suite **157 passed**.
- **2026-06-28** — Phase 9 (Unit 4, D54): new `test_tracing.py` (5 — spans are recorded on the trace; `set`
  merges attrs; `span` outside a scope is a no-op; `trace_scope` emits the `event=analysis_trace` summary
  (via `caplog`); the `_JsonFormatter` produces valid JSON with the merged trace fields) + a `test_api.py`
  end-to-end check that `/v1/analyze` emits a trace line. Suite **163 passed**.
- **2026-06-28** — Phase 9 (Unit 5, D55): `test_kestrel.py` +2 (a transient status [503 with `Retry-After`]
  → `SandboxUnavailableError` with the parsed retry_after; a transport `ConnectError` → `SandboxUnavailableError`)
  and `test_api.py` +2 (an injected `run` raising `SandboxUnavailableError` → **503** + `Retry-After`; an
  oversized `code` → **422**). Suite **167 passed**.
- **2026-06-30** — Phase 10 (D58): `test_metrics.py` +4 (`MonthlyCostMeter` on a `tmp_path` DB — accumulates and
  persists across a fresh instance; `check()` raises `MonthlyBudgetExceededError` once over the ceiling, with a
  positive `retry_after`; `add()` ignores non-positive costs; `llm._monthly_meter` is `None` when the cap is ≤ 0)
  and `test_api.py` +1 (an injected `infer` raising `MonthlyBudgetExceededError` → **503** + `Retry-After`). The
  meter is tested directly on a temp file and the 503 path via a mocked step, so the suite writes no `runs.db`.
  Suite **175 passed**.
- **2026-06-30** — Phase 10 (D60): new `test_verify.py` (5: `verify_bug` returns a `BugVerdict` with the
  function source + failing input + property reaching the judge; tier passthrough; `detected=None` falls back
  to the relation; no-key → `PipelineError`; `is_real` requires both axes) and `test_api.py` +4 (verdict
  attached to surfaced bugs; an over-inference verdict demotes but doesn't drop the bug; `verify_findings=false`
  skips the step entirely; a verification `PipelineError` degrades to unverified, not 500). `conftest.py` gained
  a 6th mocked seam (`get_verify_bug` → `SAMPLE_VERDICT` / `_default_verify`, `verify=` param). The judge is
  mocked via a fake `_client`, so the suite makes no real call. Suite **184 passed**.
- **2026-06-30** — Phase 10 (D61): import-handling tests across the suite — `test_parser.py` (module_imports +
  imported_modules captured; in-body imports counted but not re-emitted), `test_testgen.py` (`_assemble`
  re-emits the module imports, before the function), `test_results.py` (a `ModuleNotFoundError` failure is not
  a bug), `test_execution.py` (`unavailable_imports` flags only non-stdlib/non-allowlist, in order), and
  `test_api.py` +2 (a non-allowlist import → reported + sandbox skipped + no bug; a stdlib import → available +
  sandbox runs). The honest-subset test gained the `unavailable_imports` key. Suite **190 passed**.
- **2026-08-16** — Phase 10 launch (D62/D65). Suite 190 → **198**. `test_metrics.py` gains two `DailyCostMeter`
  tests (independent counter from the monthly one; a `Daily`-labelled 503 whose `Retry-After` is under a day)
  and rewrites the meter-construction test for `_budget_meters`. `test_api.py` gains four: a daily-budget 503
  carrying `period: "daily"`, a 403 when the access gate is on, acceptance of the code by header *and* query
  (plus rejection of a wrong one), and a guard that an unconfigured gate leaves the endpoint open.
  `test_web.py` gains one asserting the three new page surfaces. `conftest.py`'s `_make` gains a `settings=`
  parameter that overrides `get_settings`, which is how the gated-client tests are built. **New file
  `test_llm.py`** covers the request kwargs as a *provider contract* — the D65 lesson being that a
  hand-written fake accepts any kwargs, so 197 green tests coexisted with a live pipeline that 400'd on every
  call; this file is where that class of bug gets pinned down.

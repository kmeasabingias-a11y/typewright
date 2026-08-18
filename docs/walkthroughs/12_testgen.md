# 12 — `src/typewright/testgen.py`

## What this file is for

This file is TypeWright's **test writer**. It takes everything the earlier steps figured out
about a function — *which properties it should obey* (Phase 2) and *what kinds of inputs to feed
it* (Phase 3) — and produces an actual, ready-to-run **pytest file**: real Python code you could
save as `test_thing.py` and run.

Think of the pipeline like a kitchen. Phase 2 read the recipe and said "this dish should be
vegetarian and should serve four." Phase 3 picked the ingredients. **Phase 4 (this file) writes
the printed recipe card** — the step-by-step page a cook can actually follow. And like a good
recipe card, it has to be *complete*: it includes the dish itself (the function under test) and
the checks (the tests), so someone can run it start to finish without hunting for missing pieces.

It exposes one function: `generate_test_file(meta, analysis, plan)`, which returns a
`GeneratedTestFile` — a `source` string (the whole file), the names of the tests inside it, and a
list of any properties it couldn't turn into a test.

---

## A mental model: the AI writes the tricky bit, we build the rest

The single most important idea in this file is **who writes what**.

It would be tempting to just ask the AI: "here's a function, write me the whole test file." But
then the AI controls *everything* — the imports, and even a fresh copy of the function under test.
And an AI asked to re-type a function will sometimes quietly change it. If the function it tests
isn't *exactly* the function we were given, every result is worthless.

So we split the job (this is decision **D32**, "hybrid assembly"):

- **The AI writes only the test functions** — the genuinely creative part: turning a property like
  `slugify(slugify(s)) == slugify(s)` into a real `@given`-decorated test. That's judgement work,
  and it's what language models are good at.
- **We assemble the file ourselves** — the import header, and the *exact, untouched* source of the
  function under test (we already have it from the parser). These are the parts that simply have to
  be right, so we don't leave them to chance.

Two more ideas follow from that:

**The file is self-contained (decision D33).** We paste the function's own source into the top of
the file, so the result runs under pytest *as-is* — no "where does `slugify` come from?" The recipe
card includes the dish.

**We check it parses, but we never run it (decision D34).** Before handing the file back, we run
Python's `ast.parse()` on it — a quick, safe "is this valid Python?" check that touches nothing and
runs nothing. We deliberately do **not** *import* or *execute* the generated code here. Running
unknown generated code is risky, and isolating that risk is the entire job of a separate service
called **Kestrel** (arriving in Phase 5). So Phase 4 proves the file is *well-formed*; Phase 5 will
be the one that actually *runs* it, safely sandboxed.

---

## The whole file

```python
"""Phase 4: generate a complete, runnable pytest file from detected properties + strategies.

``generate_test_file`` takes the parser's ``FunctionMetadata``, the Phase 2
``PropertyAnalysis`` (each detected property carries a testable ``relation``), and the Phase 3
``StrategyPlan`` (a Hypothesis strategy per argument), and returns a ``GeneratedTestFile``: a
self-contained pytest module whose ``@given`` tests ASSERT each relation (D31–D35).

Hybrid assembly (D32/D33): the LLM writes ONLY the test functions; this module deterministically
assembles the final file = import header + extra imports + the VERBATIM function source
(``meta.source``) + the LLM's tests. We own the parts that must be right (imports, the function
under test); the LLM does only the creative relation->test mapping, so it can never drift the
function body. The assembled file is validated with ``ast.parse`` (D34) — a static gate only; we
deliberately do NOT import/exec the generated code in-process (running untrusted generated code
is Kestrel's job in Phase 5). A property whose companion function is unavailable (a round-trip
with no inverse in the snippet) is reported in ``skipped`` rather than producing an unrunnable
test (PROJECT_BRIEF §8 risk 3).

Mirrors ``inference.py`` / ``generation.py``: one structured Instructor call (D19) via the shared
``complete`` helper (D27/D31), low temperature + few-shot (D25). Any LLM failure, or a generated
file that will not parse, becomes a ``PipelineError`` (stage "test_generation", D15) -> HTTP 500.
"""

from __future__ import annotations

import ast

import instructor

from .config import Settings, get_settings
from .errors import PipelineError
from .llm import build_client, complete
from .models import (
    FunctionMetadata,
    GeneratedTestFile,
    GeneratedTests,
    PropertyAnalysis,
    StrategyPlan,
)

_STAGE = "test_generation"

# Imports always present in the assembled file; the LLM is told NOT to re-emit these.
_BASE_IMPORTS = ("from hypothesis import given, strategies as st", "import pytest")

_SYSTEM_PROMPT = (
    "You are a property-based-testing assistant that writes Hypothesis tests. You are given "
    "ONE Python function (its full source), the property classes it has been found to satisfy "
    "(each with a concrete, testable RELATION), and a Hypothesis strategy for each argument. "
    "Write ONE pytest test function per detected property that ASSERTS its relation, using the "
    "given strategies in an @given decorator.\n\n"
    "Rules:\n"
    "- Put each test in test_functions as complete source: an @given(...)-decorated function "
    "named test_<property_class> (add _2, _3 if a class repeats). The test's parameters are the "
    "function's arguments; map each @given keyword to that argument's provided strategy.\n"
    "- The relation may use placeholder variable names (e.g. 's', 'x'); BIND them to the "
    "function's real argument names in both @given and the assertion.\n"
    "- Call the function under test BY NAME — it is already defined in the file. Do NOT "
    "re-define it. Do NOT import hypothesis, pytest, or strategies (`given`, `st`, and `pytest` "
    "are already imported). If a test needs any OTHER import (e.g. 'import math'), put it in "
    "extra_imports.\n"
    "- Translate the relation directly: idempotence -> assert f(f(x)) == f(x); metamorphic -> "
    "assert the stated relation; invariant_preservation -> assert the structural fact; "
    "type_postcondition -> assert isinstance(...); value_postcondition -> assert the value "
    "constraint; round_trip -> assert inverse(f(x)) == x; totality -> just call the function "
    "(a no-crash test).\n"
    "- round_trip needs the inverse (companion) function. Use it ONLY if that companion is "
    "DEFINED in the given source. If it is not available, do NOT write the test — record it in "
    "skipped, e.g. 'round_trip: companion from_base64 not in the snippet'.\n"
    "- For floating-point equality use pytest.approx (or math.isclose via extra_imports), never "
    "==, to avoid false failures from rounding.\n"
    "- Keep each test small and focused on its one relation. Do not invent properties beyond "
    "those you are given."
)

_FEW_SHOT = (
    "Example.\n"
    "Function source:\n"
    "def slugify(text: str) -> str:\n"
    "    ...\n"
    "Detected properties:\n"
    "- idempotence: slugify(slugify(s)) == slugify(s)\n"
    "- metamorphic: slugify(s) == slugify(s.upper())\n"
    "- type_postcondition: isinstance(slugify(s), str)\n"
    "Argument strategies: text -> st.text()\n"
    "=> test_functions (note s is bound to the real argument `text`):\n"
    "  '@given(text=st.text())\\ndef test_idempotence(text):\\n    assert slugify(slugify(text)) == slugify(text)'\n"
    "  '@given(text=st.text())\\ndef test_metamorphic(text):\\n    assert slugify(text) == slugify(text.upper())'\n"
    "  '@given(text=st.text())\\ndef test_type_postcondition(text):\\n    assert isinstance(slugify(text), str)'\n"
    "extra_imports: []   skipped: []\n\n"
    "Example with an unavailable inverse.\n"
    "Function source: def parse_version(v: str) -> tuple[int, int, int]: ...\n"
    "Detected: round_trip: parse_version(format_version(x)) == x  (companion: format_version)\n"
    "Argument strategies: v -> st.text()\n"
    "=> test_functions: []\n"
    "   skipped: ['round_trip: companion format_version is not defined in the snippet']\n"
)


def _client() -> instructor.Instructor:
    """Build the Instructor-wrapped LiteLLM client (test seam: monkeypatch this)."""
    return build_client()


def _assemble(meta: FunctionMetadata, plan: StrategyPlan, tests: GeneratedTests) -> str:
    """Build the self-contained file: import header + function under test + the LLM's tests."""
    seen: set[str] = set()
    imports: list[str] = []
    for line in (*_BASE_IMPORTS, *plan.extra_imports, *tests.extra_imports):
        line = line.strip()
        if line and line not in seen:
            seen.add(line)
            imports.append(line)

    blocks = ["\n".join(imports), meta.source.strip()]
    blocks += [fn.strip() for fn in tests.test_functions if fn.strip()]
    return "\n\n\n".join(blocks) + "\n"


def generate_test_file(
    meta: FunctionMetadata,
    analysis: PropertyAnalysis,
    plan: StrategyPlan,
    settings: Settings | None = None,
    *,
    model_tier: str | None = None,
) -> GeneratedTestFile:
    """Generate a complete, syntactically-valid pytest file for one analyzed function."""
    settings = settings or get_settings()
    model = settings.model_for_tier(model_tier or settings.default_model_tier)

    detected = "\n".join(
        f"- {p.property_class.value}: {p.relation}"
        + (f"  (companion: {p.companion_function})" if p.companion_function else "")
        for p in analysis.detected
    ) or "(none detected)"
    strategies = "\n".join(
        f"- {s.argument} -> {s.strategy}" for s in plan.strategies
    ) or "(no arguments)"
    extra = ", ".join(plan.extra_imports) or "(none)"

    user_prompt = (
        "Write the pytest tests for this function.\n\n"
        f"Function source:\n{meta.source}\n\n"
        f"Detected properties (assert each relation):\n{detected}\n\n"
        f"Argument strategies (use these in @given):\n{strategies}\n\n"
        f"Imports already provided by the strategies: {extra}\n"
    )

    tests = complete(
        _client,
        stage=_STAGE,
        settings=settings,
        model=model,
        response_model=GeneratedTests,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT + "\n\n" + _FEW_SHOT},
            {"role": "user", "content": user_prompt},
        ],
    )

    source = _assemble(meta, plan, tests)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise PipelineError(
            _STAGE, f"generated test file is not valid Python: {exc}"
        ) from exc

    test_names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    return GeneratedTestFile(source=source, test_names=test_names, skipped=list(tests.skipped))
```

---

## Step-by-step

### The imports and constants

`ast` is Python's built-in tool for reading code *as structure* — we use it for the safety check at
the end. `complete` and `build_client` come from the shared `llm.py` (unit 10): `build_client`
makes the AI client, `complete` runs one structured AI call the same way every step does.

`_BASE_IMPORTS` are the two import lines **every** generated file gets: Hypothesis (the
property-testing library) and pytest. We add these ourselves and tell the AI *not* to repeat them —
one less thing for it to get wrong or duplicate.

### The prompts

`_SYSTEM_PROMPT` is the rulebook we hand the AI. The important rules, in plain terms:
- Write one test per property; name it `test_<property class>`.
- A relation might be written with a stand-in variable like `s` — rebind it to the function's
  *real* argument name (e.g. `text`).
- Call the function by name; **don't** re-define it; **don't** re-import Hypothesis/pytest.
- Each property class maps to a specific kind of assertion (idempotence → `f(f(x)) == f(x)`, and so
  on).
- Round-trip tests need the *inverse* function. If it isn't in the code we were given, **don't fake
  it** — list it under `skipped` instead.
- Compare floats with `pytest.approx`, not `==`, so rounding doesn't cause false alarms.

`_FEW_SHOT` shows two worked examples — including one where the inverse function is missing and the
property gets skipped — because showing beats telling for getting consistent output.

### `_client()`

The same tiny "test seam" every AI-calling module has (see unit 10). It returns the real client in
production; in tests, a fake is swapped in here so nothing touches the network.

### `_assemble(...)` — building the file (the part *we* own)

This is the deterministic heart of the hybrid approach. It glues three things together:

1. **The import header.** It walks the base imports, then any imports the *strategies* need, then
   any the *tests* need — keeping the first occurrence of each and dropping duplicates (so
   `import base64` requested by both a strategy and a test shows up once). Order is preserved.
2. **The function under test**, taken verbatim from `meta.source`. This is the line that makes the
   file self-contained (D33) and guarantees the tests run against the *real* function.
3. **The test functions** the AI wrote.

It joins them with blank lines and returns one string — the whole file.

### `generate_test_file(...)` — the public entry point

1. **Pick the model.** Same tier logic as the other steps (economy/standard/premium).
2. **Describe the job to the AI.** It formats the detected properties, the per-argument strategies,
   and the function source into one prompt.
3. **Make the call** through `complete(...)`, asking for a `GeneratedTests` back (the AI's raw
   output: the test functions, any extra imports, and the skipped list). `complete` handles the
   missing-key check and turns any failure into a `PipelineError` for stage `"test_generation"`.
4. **Assemble** the file from that output.
5. **Validate** with `ast.parse(source)`. If the assembled file isn't valid Python, that's a
   `PipelineError` (→ HTTP 500) — our generated artifact is broken, not the caller's input.
6. **Read the test names off the parsed tree**, not from what the AI claimed — these are the
   top-level `def test_...` functions actually present. Return a `GeneratedTestFile` with the
   source, those names, and the skipped list.

---

## What could go wrong

### 1. Letting the AI re-type the function under test
If the AI produced the whole file, it might subtly alter the function it's supposed to be testing —
and then the tests would be checking the wrong code, silently. We avoid this entirely by pasting the
*real* source ourselves (D32/D33). The AI literally never writes that part.

### 2. Running the generated code here
The obvious way to "really check" a test file is to import and run it. We refuse to (D34). Importing
runs module-level code; running unknown generated code in our own process is the exact danger the
Kestrel sandbox is built to contain (Phase 5). `ast.parse` gives us a safe, fast "is it valid
Python?" answer without executing a thing.

### 3. A round-trip with no inverse
Round-trip is the most powerful property, but it needs the partner function (a `format_version` to
go with `parse_version`). Often that partner isn't in the snippet we were handed. Forcing a test
anyway would produce code that can't even run. Instead the property goes into `skipped` with a
reason — honest about what we couldn't cover (this is risk 3 in `PROJECT_BRIEF.md` §8).

### 4. Trusting the AI's list of test names
We could ask the AI "what did you name the tests?" — but then the names might not match what's
actually in the file. Reading them off the parsed AST makes `test_names` a *fact about the file*,
not a claim.

### 5. Duplicate imports breaking nothing but looking sloppy
A strategy and a test might both ask for `import base64`. The dedup in `_assemble` keeps one. (Even
duplicates wouldn't crash Python, but a clean header is part of producing a file a human would trust.)

---

## Summary

`testgen.py` is Phase 4: it turns the detected properties and the strategy plan into a complete,
self-contained pytest file. The AI writes only the test functions (the creative part); we
deterministically assemble the file around them — header + the verbatim function under test + the
tests (D32/D33) — and validate it with a safe, static `ast.parse` rather than running it (D34;
execution is Kestrel's job in Phase 5). Properties it can't make runnable (a round-trip missing its
inverse) are reported as `skipped`, not faked. It calls the AI through the shared `complete` helper,
so it reads just like Phase 2 and Phase 3, and any failure surfaces as a `PipelineError` for stage
`"test_generation"`. This unit is the standalone module; wiring it into `/v1/analyze` is the next
unit.

---

## Change history

- **2026-06-15** — Created in Phase 4, Unit 1. `generate_test_file(meta, analysis, plan)` →
  `GeneratedTestFile`. Hybrid assembly (D32): the LLM writes only the `@given` test functions;
  `_assemble` builds the self-contained file by prepending the verbatim function source (D33).
  Validated with `ast.parse` only — no in-process execution; dry-run/run deferred to Kestrel
  (D34). Round-trip without an available companion is reported in `skipped` (PROJECT_BRIEF §8
  risk 3). Calls the AI through the shared `llm.complete` helper hoisted this unit (D31); stage
  `"test_generation"`. Standalone module; `/v1/analyze` wiring deferred to Unit 2 (D35). Suite
  green at 45 passed.
- **2026-06-28** — D56: `generate_test_file` now passes `max_tokens=settings.llm_max_tokens_codegen` (4096) to
  `complete`, since it emits multiple `@given` test functions and the old shared 1024 cap truncated the output
  on property-rich functions (a real 500 found testing `inflection.underscore`). `test_testgen.py` asserts the
  codegen budget is wired.
- **2026-06-28** — D57: **undefined-name guard.** Before final assembly, a deterministic `symtable` pass
  flags any generated test whose body references a global that isn't defined at module level (the function
  under test, the imports) and isn't a builtin; those tests are **dropped** (moved to `skipped`) instead of
  assembled. Kills the crash false-positive class — a hallucinated companion/helper (e.g. a round-trip
  calling an inverse not in the snippet) can never run and surface as a phantom `NameError`. Found by
  real-repo testing (`slugify`/`underscore`).
- **2026-06-30** — Phase 10 (D61): `_assemble` now re-emits `meta.module_imports` (the pasted code's
  module-level imports) into the import header — deduped against the base + LLM imports and placed before the
  function. Without it, a function relying on a top-level `import re` hit a `NameError` in the sandbox (a
  phantom crash); now its stdlib imports are present and it actually runs.

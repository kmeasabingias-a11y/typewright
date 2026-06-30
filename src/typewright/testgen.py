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
import builtins
import symtable

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
    """Build the Instructor-wrapped LiteLLM client.

    Factored out so tests can monkeypatch it with a fake that returns a known
    ``GeneratedTests`` instead of calling a real model. Passed to ``complete`` (D31),
    which preserves this seam.
    """
    return build_client()


def _assemble(meta: FunctionMetadata, plan: StrategyPlan, tests: GeneratedTests) -> str:
    """Build the self-contained file: import header + function under test + the LLM's tests.

    Imports are order-preserving deduped (the base imports, then the strategies' extra imports,
    then the tests' extra imports) so e.g. ``import base64`` requested by both a strategy and a
    test appears once. We own this assembly — the LLM never re-emits the function or the header.
    """
    seen: set[str] = set()
    imports: list[str] = []
    # meta.module_imports re-emits the pasted code's module-level imports (D61): the parser keeps
    # only the function body in meta.source, so without these a function relying on a top-level
    # `import re` would hit a NameError in the sandbox. Deduped against the base + LLM imports.
    for line in (*_BASE_IMPORTS, *meta.module_imports, *plan.extra_imports, *tests.extra_imports):
        line = line.strip()
        if line and line not in seen:
            seen.add(line)
            imports.append(line)

    blocks = ["\n".join(imports), meta.source.strip()]
    blocks += [fn.strip() for fn in tests.test_functions if fn.strip()]
    return "\n\n\n".join(blocks) + "\n"

_BUILTIN_NAMES = frozenset(dir(builtins))


def _func_name(src: str) -> str | None:
    """The name of the first top-level function in a test-function source string."""
    try:
        for node in ast.parse(src).body:
            if isinstance(node, ast.FunctionDef):
                return node.name
    except SyntaxError:
        return None
    return None


def _unresolved_names(table: symtable.SymbolTable, module_names: set[str]) -> set[str]:
    """Names referenced (not assigned) in this scope or any nested scope that resolve to global
    but are defined neither at module level nor as a builtin — i.e. undefined references."""
    found: set[str] = set()
    for sym in table.get_symbols():
        name = sym.get_name()
        if (
            sym.is_global()
            and sym.is_referenced()
            and not sym.is_assigned()
            and name not in module_names
            and name not in _BUILTIN_NAMES
        ):
            found.add(name)
    for child in table.get_children():
        found |= _unresolved_names(child, module_names)
    return found

def _tests_with_undefined_names(source: str) -> dict[str, set[str]]:
    """Map each ``test_*`` function in the assembled module to the undefined names it references."""
    try:
        top = symtable.symtable(source, "<typewright-tests>", "exec")
    except SyntaxError:
        return {}  # let the normal ast.parse gate raise a PipelineError
    module_names = set(top.get_identifiers())
    result: dict[str, set[str]] = {}
    for child in top.get_children():
        if child.get_type() == "function" and child.get_name().startswith("test_"):
            undef = _unresolved_names(child, module_names)
            if undef:
                result[child.get_name()] = undef
    return result


def _drop_unresolved_tests(
    meta: FunctionMetadata, plan: StrategyPlan, tests: GeneratedTests
) -> GeneratedTests:
    """Drop generated tests that reference an undefined name (D57). A hallucinated companion/helper
    (e.g. a round-trip calling an inverse not in the snippet) would crash with NameError and surface
    as a phantom bug; dropping it before assembly removes that false-positive class. Dropped tests are
    recorded in ``skipped``. Skipped entirely when a star-import makes name resolution unreliable."""
    if any("import *" in imp for imp in (*plan.extra_imports, *tests.extra_imports)):
        return tests
    bad = _tests_with_undefined_names(_assemble(meta, plan, tests))
    if not bad:
        return tests
    kept: list[str] = []
    skipped = list(tests.skipped)
    for fn in tests.test_functions:
        name = _func_name(fn)
        if name in bad:
            skipped.append(
                f"{name}: dropped — references undefined name(s): {', '.join(sorted(bad[name]))}"
            )
        else:
            kept.append(fn)
    return GeneratedTests(
        test_functions=kept, extra_imports=list(tests.extra_imports), skipped=skipped
    )


def generate_test_file(
    meta: FunctionMetadata,
    analysis: PropertyAnalysis,
    plan: StrategyPlan,
    settings: Settings | None = None,
    *,
    model_tier: str | None = None,
) -> GeneratedTestFile:
    """Generate a complete, syntactically-valid pytest file for one analyzed function.

    Raises ``PipelineError`` (stage "test_generation") if the LLM call fails, or if the
    assembled file will not parse (D34) — either way the caller's input was fine, so this
    surfaces as a 500 (D15).
    """
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
        max_tokens=settings.llm_max_tokens_codegen,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT + "\n\n" + _FEW_SHOT},
            {"role": "user", "content": user_prompt},
        ],
    )

    tests = _drop_unresolved_tests(meta, plan, tests)
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
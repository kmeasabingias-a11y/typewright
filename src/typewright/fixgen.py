"""Phase 6: suggest a corrected function for the bugs found, verified by re-running the tests.

``suggest_fix`` takes the parser's ``FunctionMetadata`` and the ``BugReport`` from running the
generated tests, and returns a ``ProposedFix``: a corrected version of the function plus a
one-line explanation. It does ONLY the LLM call (D44).

Verification is separate, and deliberately so (D41 — seams exist for I/O, pure helpers don't):
the route swaps the corrected function into the SAME test file (``build_fix_file``), re-runs it
through the existing run-tests seam, and ``finalize`` reads the resulting ``BugReport`` into a
``FixSuggestion``. Judging the fix against the SAME @given tests — not new ones — is what makes a
green re-run real evidence the bug is gone, rather than the model re-grading its own work. We
splice only the function definition, so the LLM can never drift the tests or imports.

A single attempt only (D45): if the re-run still fails (or can't be completed), the fix is
surfaced with ``verified=False`` ("no confident fix"), never iterated — the agentic
draft->verify->refine loop is deferred exactly as D19 deferred it for detection. The fix step is
best-effort and downstream of an already-valid analysis, so its failures degrade the suggestion
rather than failing the request (the route catches them); only the LLM call itself, when reached,
wraps a hard failure as ``PipelineError`` (stage "fix_suggestion", D15).

Mirrors ``inference``/``generation``/``testgen``: one structured Instructor call via the shared
``complete`` helper (D27/D31), low temperature, few-shot.
"""

from __future__ import annotations

import ast

import instructor

from .config import Settings, get_settings
from .llm import build_client, complete
from .models import (
    BugReport,
    FixSuggestion,
    FunctionMetadata,
    GeneratedTestFile,
    ProposedFix,
)

_STAGE = "fix_suggestion"

_SYSTEM_PROMPT = (
    "You are a debugging assistant. You are given ONE Python function and a list of BUGS that "
    "property-based tests found in it — each bug names the violated property/relation, the exact "
    "failing input, and the error. Produce a CORRECTED version of the function that fixes ALL the "
    "bugs while preserving the function's intended behaviour on inputs that already worked.\n\n"
    "Rules:\n"
    "- Keep the SAME function name and signature — your function must be a drop-in replacement.\n"
    "- Return ONLY the corrected function definition as source in corrected_source: no imports, "
    "no tests, no markdown fences, no prose around it.\n"
    "- Fix the DEFECT the bugs reveal; do not change what the function is meant to do. Its intent "
    "comes from its name, signature, and docstring.\n"
    "- A bug whose input is the empty/zero/boundary case usually means a missing guard — handle "
    "it explicitly rather than letting it crash.\n"
    "- If a reported bug looks like the PROPERTY is wrong (a false alarm against already-correct "
    "code) rather than the function, still return the best correct implementation you can and say "
    "so in the explanation.\n"
    "- Give a one-sentence explanation of what was wrong and what you changed."
)

_FEW_SHOT = (
    "Example.\n"
    "Function:\n"
    "def first_word(text: str) -> str:  # first whitespace word, or '' if none\n"
    "    return text.split()[0]\n"
    "Bugs found:\n"
    "- test_totality: input text=''; error IndexError; violated relation: "
    "first_word(text) does not raise (crash)\n"
    "=> corrected_source:\n"
    "def first_word(text: str) -> str:  # first whitespace word, or '' if none\n"
    "    words = text.split()\n"
    '    return words[0] if words else ""\n'
    'explanation: Returned "" when there are no words instead of indexing an empty list.\n'
)


def _client() -> instructor.Instructor:
    """Build the Instructor-wrapped LiteLLM client.

    Factored out so tests can monkeypatch it with a fake that returns a known ``ProposedFix``
    instead of calling a real model. Passed to ``complete`` (D31), which preserves this seam.
    """
    return build_client()


def suggest_fix(
    meta: FunctionMetadata,
    report: BugReport,
    settings: Settings | None = None,
    *,
    model_tier: str | None = None,
) -> ProposedFix:
    """Ask the model for a corrected function that fixes the bugs in ``report``.

    Does ONLY the LLM call — verification (swap + re-run) is the route's job (D41). Raises
    ``PipelineError`` (stage "fix_suggestion") if the call fails; the route treats fix
    suggestion as best-effort and catches that so a failed suggestion never sinks the
    already-valid analysis (D44).
    """
    settings = settings or get_settings()
    model = settings.model_for_tier(model_tier or settings.default_model_tier)

    bug_lines = "\n".join(
        f"- {b.test_name}: input {b.failing_input or '(none captured)'}; "
        f"error {b.error}; violated relation: {b.violated_property} ({b.severity.value})"
        for b in report.bugs
    ) or "(no bugs)"

    user_prompt = (
        "Fix this function so every bug below is resolved.\n\n"
        f"Function source:\n{meta.source}\n\n"
        f"Bugs found by the property tests:\n{bug_lines}\n\n"
        "Return the corrected function (same name and signature) and a one-sentence explanation."
    )

    return complete(
        _client,
        stage=_STAGE,
        settings=settings,
        model=model,
        response_model=ProposedFix,
        max_tokens=settings.llm_max_tokens_codegen,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT + "\n\n" + _FEW_SHOT},
            {"role": "user", "content": user_prompt},
        ],
    )


def build_fix_file(
    test_file: GeneratedTestFile, meta: FunctionMetadata, fix: ProposedFix
) -> GeneratedTestFile | None:
    """Splice the corrected function into the test file in place of the original.

    Returns a new ``GeneratedTestFile`` whose source is the original file with ONLY the
    function-under-test replaced by ``fix.corrected_source`` — same imports, same @given tests —
    so the fix is judged by the SAME oracle (D45). ``testgen`` embedded the function as
    ``meta.source.strip()`` verbatim (D32/D33), so an exact, single replacement is reliable.

    Returns ``None`` when the swap can't be made safely — the corrected code doesn't parse,
    defines no top-level function named ``meta.name``, the original block isn't found, or the
    reassembled file doesn't parse. The caller then reports ``verified=False`` rather than
    shipping unrunnable code to the sandbox or failing the request.
    """
    corrected = fix.corrected_source.strip()
    try:
        corrected_tree = ast.parse(corrected)
    except SyntaxError:
        return None
    defined = {
        node.name
        for node in corrected_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if meta.name not in defined:
        return None

    original = meta.source.strip()
    if original not in test_file.source:
        return None
    swapped = test_file.source.replace(original, corrected, 1)

    try:
        ast.parse(swapped)
    except SyntaxError:
        return None
    return GeneratedTestFile(
        source=swapped, test_names=test_file.test_names, skipped=test_file.skipped
    )


def finalize(fix: ProposedFix, report: BugReport | None) -> FixSuggestion:
    """Build the verified ``FixSuggestion`` from the proposal and the verification re-run.

    ``report is None`` means the re-run could not be completed (the swap failed, or the sandbox
    timed out / errored) — degrade to ``verified=False`` rather than failing the request (D44).
    The fix is verified only when the re-run is unambiguously green: no bugs, no timeout,
    exit_code 0.
    """
    verified = bool(
        report is not None
        and not report.bugs
        and not report.timed_out
        and report.exit_code == 0
    )
    return FixSuggestion(
        code=fix.corrected_source.strip(),
        explanation=fix.explanation,
        verified=verified,
        tests_passed=report.tests_passed if report else 0,
        tests_failed=report.tests_failed if report else 0,
    )
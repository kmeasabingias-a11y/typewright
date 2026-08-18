# 17 — `src/typewright/fixgen.py`

## What this file is for

This file is TypeWright's **fix suggester**. Once the pipeline has actually *run* the generated
tests and found bugs (Phase 5), this last step asks the AI: "here's the function, here are the
exact inputs that broke it — can you fix it?" Then, instead of taking the AI's word for it, it
**proves** the fix by re-running the very same tests against the corrected code.

Back to the kitchen analogy. Phase 2 read the recipe's promises; Phase 3 picked the ingredients;
Phase 4 wrote the recipe card; Phase 5 cooked it and found the dish was too salty on certain
inputs. **Phase 6 (this file) proposes a corrected recipe — and then cooks *that* one to confirm
it's no longer too salty.** A suggested fix that hasn't been re-tasted is just an opinion; a fix
that passes the same taste-test the original failed is evidence.

It exposes three small functions:

- `suggest_fix(meta, report)` — the AI call. Returns a `ProposedFix` (corrected source + a
  one-line explanation). **Only the LLM call** — it does no running.
- `build_fix_file(test_file, meta, fix)` — deterministically swaps the corrected function into the
  existing test file, keeping the tests and imports exactly as they were. Returns the new file, or
  `None` if the swap can't be made safely.
- `finalize(fix, report)` — combines the proposal with the re-run's result into the final
  `FixSuggestion` (with the all-important `verified` flag).

The *route* (`main.py`) ties these together; this file deliberately keeps the AI call separate
from the running, because running is sandbox work (that separation is decision **D41**).

---

## A mental model: don't trust the fix — re-test it with the SAME tests

The single most important idea here is **how we know a fix actually works**.

A naive fix feature would ask the AI to fix the function and just believe it. But an AI's "I fixed
it" is exactly the kind of confident-but-wrong claim this whole project exists to catch. Two
tempting-but-wrong ways to "verify":

- **Let the AI grade itself** ("does your fix look right?") — circular; it's the same model that
  wrote the fix.
- **Generate *new* tests for the fix** — also circular; new tests written for the fix can be biased
  toward passing.

So we do the one thing that's genuinely independent (decision **D45**): we keep the **exact same
property tests** that *found* the bug, swap **only** the function under test for the AI's corrected
version, and run it again in the sandbox. The assertions are byte-for-byte the ones that failed
before. If they now pass, the bug is really gone — nothing changed except the code. We mark the fix
`verified` only when that re-run is green (no bugs, didn't time out, exited cleanly). If it still
fails, we're honest: the suggestion is returned with `verified=false` — the brief's "no confident
fix."

Two more ideas follow:

**We own the swap, not the AI (decision D45).** `build_fix_file` does the function-swap
deterministically — find the original function (Phase 4 pasted it in verbatim, so it's right there),
replace just that block, leave the imports and tests untouched. The AI never gets to rewrite the
tests or the header. If the AI's corrected code doesn't parse, or doesn't define a function of the
right name, the swap returns `None` and the fix is reported unverified — never shipped to the sandbox
broken.

**The fix step is best-effort — it never sinks the request (decision D44).** By the time we get
here, the real value (`bugs_found`) is already in hand. So unlike the earlier mandatory steps (where
any failure is a 500), *anything* going wrong in the fix step just means "no/unverified suggestion,"
not an error. A failed AI call → no suggestion; an unrunnable fix or a slow verification → unverified.
The caller still gets their bugs.

**It's opt-in (decision D44).** The fix step costs a fourth AI call **plus a second sandbox run**, so
it only runs when the caller asks (`include_fix_suggestion: true`) *and* there are bugs to fix. The
web demo (Phase 8) will surface this as a checkbox — "let the user decide per request."

---

## The whole file

```python
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
    """Build the Instructor-wrapped LiteLLM client (test seam: monkeypatch this)."""
    return build_client()


def suggest_fix(
    meta: FunctionMetadata,
    report: BugReport,
    settings: Settings | None = None,
    *,
    model_tier: str | None = None,
) -> ProposedFix:
    """Ask the model for a corrected function that fixes the bugs in ``report`` (LLM call only)."""
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
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT + "\n\n" + _FEW_SHOT},
            {"role": "user", "content": user_prompt},
        ],
    )


def build_fix_file(
    test_file: GeneratedTestFile, meta: FunctionMetadata, fix: ProposedFix
) -> GeneratedTestFile | None:
    """Splice the corrected function into the test file in place of the original (same tests)."""
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
    """Build the verified ``FixSuggestion`` from the proposal and the verification re-run."""
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
```

(The docstrings of the three functions are abbreviated above for readability; the live file
carries the full versions.)

---

## Step-by-step

### The imports and prompts

`ast` is Python's read-code-as-structure tool — used in `build_fix_file` to check the AI's fix is
valid Python and defines the right function. `complete` / `build_client` come from the shared
`llm.py` (unit 10), the same AI plumbing every step uses.

`_SYSTEM_PROMPT` is the rulebook: fix **all** the bugs, keep the **same name and signature** (so the
fix is a drop-in replacement), return **only** the function (no imports, no tests), fix the *defect*
without changing what the function is meant to do, and — nicely — if a "bug" is actually a wrong
*property* against already-correct code, say so in the explanation rather than mangling good code.

`_FEW_SHOT` shows one worked example: a `first_word` that crashes on the empty string, corrected to
guard the empty case. Showing beats telling for consistent output.

### `suggest_fix(...)` — the AI call (and *only* the AI call)

1. Pick the model (same economy/standard/premium tier logic as every step).
2. Format the bugs into a plain list — each line carries the failing input, the error, and the
   violated relation, so the model sees *exactly* what broke and why.
3. Call through `complete(...)`, asking for a `ProposedFix` back. `complete` does the missing-key
   check and turns any failure into a `PipelineError` for stage `"fix_suggestion"`.

Notice what it does **not** do: it doesn't run anything. Running is the sandbox's job, kept separate
on purpose (D41) so this function stays a pure, easily-mocked AI call.

### `build_fix_file(...)` — the swap we own

This is the deterministic heart of verification. Given the original test file, the function's
metadata, and the AI's fix, it produces a *new* test file that is identical except the function under
test is replaced:

1. **Check the fix parses** (`ast.parse`). Garbage in → return `None`.
2. **Check it defines the right function** — a top-level `def` (or `async def`) of the original name.
   A fix that renamed or didn't define the function → `None`.
3. **Replace exactly one block.** Phase 4 pasted the function in as `meta.source.strip()` *verbatim*
   and placed it before the tests (D32/D33), so the original text is present and its first occurrence
   is the function itself. A single `.replace(original, corrected, 1)` swaps just that block, leaving
   the import header and every test untouched.
4. **Re-validate the whole file** (`ast.parse` again). If the splice somehow produced invalid Python →
   `None`.

Returning `None` (rather than raising) is deliberate: a fix we can't safely assemble simply can't be
verified, so the caller reports it unverified — it's not a server error (that's the best-effort rule,
D44).

### `finalize(...)` — the verdict

After the route re-runs the swapped file and parses the result, `finalize` turns the proposal +
that `BugReport` into the `FixSuggestion`. `verified` is `True` **only** when the re-run is
unambiguously green: no bugs, didn't time out, exit code 0. A `report` of `None` (the swap failed, or
the verification run timed out / errored) → `verified=False`. The `code`, `explanation`,
pass/fail counts, and the standing "review carefully" disclaimer round out the result.

### Who calls these three?

The route in `main.py` (unit 06), in a small `_maybe_suggest_fix` helper: only when
`include_fix_suggestion` is set **and** bugs were found, it calls `suggest_fix`, then
`build_fix_file`, then re-runs through the **same** sandbox dependency the first run used, then
`finalize`. Any failure along the way degrades to a `null` or `verified=false` suggestion — the
analysis (`bugs_found`) is never lost.

---

## What could go wrong

### 1. Believing a fix that doesn't actually work
The whole danger of an AI fix is a confident wrong answer. We never trust the model's say-so — we
re-run the **same** tests that found the bug against the corrected code (D45). Green re-run = real
evidence; anything else = `verified=false`.

### 2. Verifying with *different* tests
If we let the AI write fresh tests for its own fix, those tests could be quietly biased toward
passing. Re-using the original property tests, byte-for-byte, removes that bias — the only thing that
changed between "failing" and "passing" is the function body.

### 3. Letting the AI rewrite the tests or imports
`build_fix_file` swaps *only* the function definition; the AI's output is dropped into the existing
file, which keeps its original header and tests. The AI can't touch the oracle.

### 4. A malformed fix crashing the request
A fix that doesn't parse, or renames the function, would make an unrunnable file. Instead of erroring,
`build_fix_file` returns `None` and the fix is reported `verified=false`. Combined with the route's
best-effort handling (D44), a bad fix never turns into a 500 — the caller still gets `bugs_found`.

### 5. The optional step sinking the whole analysis
Every earlier stage is all-or-nothing (a failure is a 500). Fix suggestion is the deliberate
exception (D44): it runs *after* a complete, valid analysis, so a failed AI call, an unrunnable fix,
or a slow verification all degrade the suggestion rather than failing the request. The bugs the caller
came for are already in hand and must survive.

### 6. Paying for a fix nobody asked for
Fix suggestion is the most expensive step (an AI call **plus** a second sandbox run). It's opt-in
(`include_fix_suggestion`, default false) and skipped entirely when there are no bugs — so the common
"just show me the bugs" call costs nothing extra (D44).

---

## Summary

`fixgen.py` is Phase 6: it proposes a corrected function for the bugs found and **proves** it by
re-running the same property tests against it. `suggest_fix` is the AI call (and only that);
`build_fix_file` deterministically swaps the corrected function into the existing test file (we own
that splice so the AI can't drift the tests); `finalize` reads the re-run's result into a
`FixSuggestion` whose `verified` flag is true only when the re-run is green (D45). It's a single
attempt — no refine loop (mirroring D19). The whole step is **opt-in and best-effort** (D44): it runs
only when asked and only when there are bugs, and any failure degrades the suggestion instead of
failing the request, so `bugs_found` is never lost. It calls the AI through the shared `complete`
helper, so it reads just like Phases 2–4, with stage `"fix_suggestion"`.

---

## Change history

- **2026-06-25** — Created in Phase 6. **Unit 1** added the standalone module: `suggest_fix`
  (the fourth LLM call → `ProposedFix`, stage `"fix_suggestion"`, via the shared `complete` helper,
  D44), `build_fix_file` (the deterministic, `ast`-validated function-swap that keeps the same tests/
  imports, returning `None` when it can't splice safely, D45), and `finalize` (proposal + verification
  `BugReport` → `FixSuggestion`, `verified` only on a green re-run). New models `ProposedFix` /
  `FixSuggestion` (unit 03). **Unit 2** wired it into `/v1/analyze` (unit 06): opt-in via
  `include_fix_suggestion` (default false), best-effort — any failure degrades `fix_suggestion`
  rather than 500-ing the request, reusing the existing `get_run_tests` seam for the verification
  re-run (D44). `test_fixgen.py` (13 tests) + 6 new API tests. Suite green at 92 passed. Live
  golden-set measurement (the ~60% exit metric) is the remaining Phase-6 step.
- **2026-06-28** — D56: `suggest_fix` now passes `max_tokens=settings.llm_max_tokens_codegen` (4096) to
  `complete` — it emits a corrected function and shouldn't be truncated by the old shared 1024 cap (same
  per-stage token fix as testgen, found by real-repo testing).

# 22 — `src/typewright/analysis.py`

## What this file is for

This file runs **one function through the whole TypeWright pipeline** and returns a tidy result the bot
can comment on. It's the bridge between "the API endpoint" (which does the same steps but for a single
pasted function over HTTP) and "the GitHub worker" (which needs to do it for each changed function in a
PR, off in the background).

Rather than call the HTTP route from the worker, this file reuses the same underlying step functions
directly — detect properties, generate strategies, generate the test file, run it in Kestrel, parse the
result — and then attempts a verified fix if bugs were found. Out comes a single `FunctionFinding`.

It exposes one function: `analyze_one(meta)` → a `FunctionFinding`.

---

## A mental model: the same assembly line, packaged for the bot

The `/v1/analyze` endpoint (unit 06) already runs an assembly line: function → properties → strategies →
test file → run in sandbox → bugs → optional fix. `analyze_one` is that **same assembly line**, but:
- it takes a parsed function and returns just `{name, bugs, fix}` (a `FunctionFinding`) instead of a full
  HTTP response, and
- it **always tries to fix** when there are bugs (the bot's whole value is bugs *plus* fixes), and
- it's resilient about the fix: if fixing fails for any reason, you still get the bugs (a fix is a bonus,
  not a requirement).

Why not just call the endpoint? Because the worker isn't making an HTTP request — it's a background job.
Reusing the step functions directly is cleaner and avoids spinning the web layer.

---

## The whole file

```python
"""Phase 7: run one changed function through the full analysis pipeline -> a FunctionFinding.

The GitHub worker's per-function step. Reuses the same pipeline functions the HTTP route uses
(infer -> strategies -> test file -> run in sandbox -> parse), then always attempts a verified
fix when bugs are found (the bot's value is bugs + fixes). Unlike the all-or-nothing HTTP route,
a fix-step failure here just yields a finding with no fix (the bugs are what matter, D44). A
mandatory-stage ``PipelineError`` still propagates — the worker catches it per function so one
bad function doesn't sink the PR.
"""

from __future__ import annotations

from .config import Settings, get_settings
from .errors import PipelineError
from .execution import run_tests
from .fixgen import build_fix_file, finalize, suggest_fix
from .generation import generate_strategies
from .inference import infer_properties
from .models import FunctionFinding, FunctionMetadata
from .results import parse_results
from .testgen import generate_test_file


def analyze_one(meta: FunctionMetadata, settings: Settings | None = None) -> FunctionFinding:
    """Detect -> strategies -> tests -> run -> parse, then a verified fix when bugs are found."""
    settings = settings or get_settings()
    budget = settings.kestrel_timeout_seconds

    properties = infer_properties(meta, settings)
    plan = generate_strategies(meta, properties, settings)
    test_file = generate_test_file(meta, properties, plan, settings)
    report = parse_results(run_tests(test_file, timeout_seconds=budget, settings=settings), properties)

    fix_suggestion = None
    if report.bugs:
        try:
            proposed = suggest_fix(meta, report, settings)
            fix_file = build_fix_file(test_file, meta, proposed)
            verify_report = None
            if fix_file is not None:
                vresult = run_tests(fix_file, timeout_seconds=budget, settings=settings)
                if not vresult.timed_out:
                    verify_report = parse_results(vresult, properties)
            fix_suggestion = finalize(proposed, verify_report)
        except PipelineError:
            fix_suggestion = None  # best-effort: bugs still get reported without a fix

    return FunctionFinding(function_name=meta.name, bugs=report.bugs, fix_suggestion=fix_suggestion)
```

---

## Step-by-step

1. **Settings + budget.** Resolve settings and the per-run sandbox time budget.
2. **The mandatory chain.** Detect properties (`infer_properties`), generate input strategies
   (`generate_strategies`), assemble the pytest file (`generate_test_file`), run it in Kestrel
   (`run_tests`), and read the result into a `BugReport` (`parse_results`). These are the same functions
   units 09–15 cover; nothing new is invented here.
3. **The optional fix.** Only if bugs were found: ask the model for a corrected function (`suggest_fix`),
   splice it into the same test file (`build_fix_file`), re-run that in the sandbox, parse the re-run, and
   `finalize` into a `FixSuggestion` whose `verified` flag is true only if the re-run was clean. This is
   the Phase-6 flow (units 17), run end-to-end here.
4. **Best-effort fix.** The whole fix block is wrapped in `try/except PipelineError`: if any fix step
   fails, we set the fix to `None` and still return the bugs. A fix is a bonus.
5. **Return** a `FunctionFinding` with the name, the bugs, and the fix (or `None`).

Note one subtlety: the *mandatory* chain (steps 2) can raise `PipelineError`, and `analyze_one` lets it
propagate. That's deliberate — the **worker** catches it per function, so one un-analyzable function
doesn't kill the whole PR (it just gets skipped).

---

## What could go wrong

### 1. Reimplementing the pipeline (and drifting from the endpoint)
If the worker had its own copy of the analysis logic, it could drift from the HTTP route's behavior.
`analyze_one` reuses the exact same step functions, so the bot and the API analyze identically.

### 2. A fix failure hiding the bugs
The bugs are the point; a fix is extra. Wrapping just the fix block in `try/except` (best-effort, D44)
means a flaky fix step never costs you the bug report.

### 3. A slow function blocking everything
`run_tests` is bounded by the sandbox budget, and a verification re-run that times out simply yields an
unverified fix rather than hanging. (And the worker runs this whole thing on a thread, unit 23, so it
doesn't block the event loop.)

### 4. One bad function sinking the PR
A mandatory-stage error here *does* raise — but the worker catches it per function. So a single function
the model can't process is logged and skipped; the other functions still get analyzed and commented.

---

## Summary

`analysis.py`'s `analyze_one` is the worker's per-function step: it runs the same detect → strategies →
tests → run → parse chain the HTTP route uses, then best-effort-attempts a verified fix when bugs exist,
and returns a `FunctionFinding`. It reuses the existing step functions (no duplication), keeps the fix
optional so bugs are never lost (D44), and lets mandatory-stage errors propagate for the worker to catch
per function. Part of decision **D48**.

---

## Change history

- **2026-06-25** — Created in Phase 7, Unit 5 (D48). `analyze_one(meta)` reuses the pipeline step functions
  (infer/generate/testgen/run/parse) + the Phase-6 verified-fix flow (suggest_fix/build_fix_file/run/finalize),
  best-effort on the fix (PipelineError → no fix, bugs kept), mandatory stages propagate for the worker to
  catch. Returns a `FunctionFinding`. Ran live in the Phase-7 smoke (2 Kestrel executes: bug found, then fix
  verified).

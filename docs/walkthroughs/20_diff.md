# 20 — `src/typewright/diff.py`

## What this file is for

This file answers one question: **which functions did this pull request actually change?**

A PR might touch a 500-line file but only edit one function. We don't want to re-test the whole file —
just the function(s) that changed. This file takes the PR's diff and the file's new content and works
out exactly which top-level functions were touched, handing back the now-familiar `FunctionMetadata`
for each (the same shape the rest of the pipeline already consumes).

It's pure logic — no GitHub, no network. The worker fetches the diff and the file text (unit 19) and
passes them in here.

It exposes:
- `changed_line_numbers(patch)` → the set of new-file line numbers that were added/modified
- `changed_functions(content, changed_lines)` → the `FunctionMetadata` for each function that overlaps

---

## A mental model: highlight the edited lines, then see which functions they fall inside

Imagine printing the new version of the file, then taking a highlighter to every line the PR added or
changed. Now look at where the highlighter marks land: if any of them fall **inside a function's body**
(between its `def` line and its last line), that function changed and is worth testing. If a function
has no highlighter marks anywhere in it, the PR didn't touch it — skip it.

That's the whole idea, in two steps:
1. **Which lines got highlighted?** Read the diff. A unified diff marks added lines with `+`. Those line
   numbers (in the *new* file) are what changed. → `changed_line_numbers`.
2. **Which functions contain a highlighted line?** Parse the file, find each top-level function's line
   range, and keep the ones that overlap the highlighted set. → `changed_functions`.

For step 2 we reuse the parser we already trust (unit 05) to turn each chosen function into a
`FunctionMetadata`, so the worker can feed it straight into the existing pipeline.

---

## The whole file

```python
"""Phase 7: from a PR's unified-diff patches to the changed top-level functions (no I/O).

Two pure steps the worker composes per changed .py file:

* ``changed_line_numbers`` — read a GitHub ``patch`` (concatenated unified-diff hunks) and return
  the set of NEW-file line numbers that were added or modified.
* ``changed_functions`` — parse the file's NEW content and return a ``FunctionMetadata`` for each
  top-level function whose line span intersects those changed lines.

Together they answer "which functions in this PR actually changed?" so the worker analyzes only
those, not the whole file. Both are pure (text in, data out) and unit-tested directly; fetching
the file content + patch is the worker's I/O (Unit 5).
"""

from __future__ import annotations

import ast
import re

from .errors import TypeWrightError
from .models import FunctionMetadata
from .parser import parse_function

# Unified-diff hunk header: "@@ -oldStart[,oldCount] +newStart[,newCount] @@".
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@", re.MULTILINE)


def changed_line_numbers(patch: str | None) -> set[int]:
    """Return the NEW-file line numbers added/modified by a unified-diff ``patch``.

    Walks each ``@@ ... +start[,count] @@`` hunk: '+' lines are new/modified (recorded), ' '
    context lines advance the new-file counter, '-' lines (old only) don't. A ``None``/empty
    patch (e.g. a binary or rename-only file) yields an empty set.
    """
    if not patch:
        return set()
    changed: set[int] = set()
    new_line = 0
    in_hunk = False
    for raw in patch.splitlines():
        m = _HUNK.match(raw)
        if m:
            new_line = int(m.group("start"))
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if raw.startswith("+"):
            changed.add(new_line)
            new_line += 1
        elif raw.startswith("-"):
            continue  # old-file line; does not advance the new-file counter
        elif raw.startswith("\\"):
            continue  # "\ No newline at end of file"
        else:  # context line (leading ' ', or a blank line)
            new_line += 1
    return changed


def changed_functions(content: str, changed_lines: set[int]) -> list[FunctionMetadata]:
    """Top-level functions in ``content`` whose line span intersects ``changed_lines``.

    Returns a ``FunctionMetadata`` (via the existing parser) for each. A file that doesn't parse
    (Python 2, partial, syntax error) yields ``[]`` — it is skipped, not an error. The span
    includes any decorators so a decorator-only change still counts.
    """
    if not changed_lines:
        return []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    selected: list[FunctionMetadata] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        end = node.end_lineno or node.lineno
        if any(start <= ln <= end for ln in changed_lines):
            try:
                selected.append(parse_function(content, node.name))
            except TypeWrightError:
                continue  # can't isolate it as a standalone function — skip
    return selected
```

---

## Step-by-step

### Reading a unified diff (`changed_line_numbers`)
A `patch` is a series of **hunks**. Each hunk starts with a header like `@@ -3,4 +3,6 @@`, where `+3,6`
means "in the new file, this hunk starts at line 3 and spans 6 lines." The regex `_HUNK` pulls out that
new-file start number.

After a header, each line of the hunk is prefixed by one character:
- `+` → a line that's **new** in this version → record its line number, then advance.
- (space) → a **context** line (unchanged, shown for orientation) → just advance the counter.
- `-` → a line removed from the *old* file → it doesn't exist in the new file, so don't advance the
  new-file counter and don't record it.
- `\` → the special `\ No newline at end of file` marker → ignore.

Walking the hunk this way keeps an accurate running new-file line number and collects exactly the lines
that were added or modified. A `None` or empty patch (a binary file, a pure rename) → empty set.

### Picking the changed functions (`changed_functions`)
- If nothing changed, return `[]` immediately.
- Parse the file with `ast`. If it doesn't parse (e.g. it's Python 2, or a partial/odd file), return
  `[]` — we skip files we can't understand rather than erroring.
- Walk the **top-level** nodes; for each function (`def` / `async def`), compute its line span. The start
  is the earliest of its `def` line and any decorator lines (so changing a `@decorator` counts); the end
  is `end_lineno` (the function's last line).
- If *any* changed line falls within `[start, end]`, this function was touched → use the existing
  `parse_function` to build its `FunctionMetadata` and add it.
- `parse_function` is wrapped in `try/except TypeWrightError` for safety, though since the name came from
  the same parse it should always succeed.

---

## What could go wrong

### 1. Off-by-one line counting
The easy bug is miscounting which lines are new — e.g. advancing the counter on a `-` (removed) line.
Removed lines don't exist in the new file, so they must *not* advance the new-file counter. The walk
handles `+`, space, `-`, and `\` distinctly for exactly this reason.

### 2. Analyzing the whole file when one function changed
Without this step the worker would re-test every function in a touched file — slow and noisy. Intersecting
changed lines with function spans narrows it to just what the PR affected.

### 3. Crashing on a file we can't parse
PRs contain all sorts of files. `changed_functions` only looks at `.py` content the worker hands it, and
if even that doesn't parse, it returns `[]` (skip) instead of raising — one weird file shouldn't sink the
PR.

### 4. Missing a decorator change
A change to `@app.route(...)` above a function is a real change to that function. Including decorator
lines in the span (`min([def] + [decorators])`) catches it.

### 5. Pure deletions
If a PR only *removes* lines from a function (no additions), there's no `+` line inside it, so it won't be
flagged. That's a known, accepted limitation for the MVP — the common case (adding/modifying code) is
covered, and a pure deletion rarely needs re-testing on its own.

---

## Summary

`diff.py` turns a PR diff into the set of functions actually worth analyzing: `changed_line_numbers`
reads the unified-diff hunks to find which new-file lines were added/modified, and `changed_functions`
parses the file, intersects each top-level function's line span with those lines, and returns a
`FunctionMetadata` (via the existing parser) for the overlaps — skipping files that don't parse. Pure and
unit-tested; the worker supplies the diff + content. This is part of decision **D48**.

---

## Change history

- **2026-06-25** — Created in Phase 7, Unit 3 (D48). `changed_line_numbers` (unified-diff hunk walk →
  new-file added/modified lines) + `changed_functions` (AST top-level-function spans ∩ changed lines →
  `FunctionMetadata` via `parse_function`, skips unparseable files, includes decorator lines). Pure; the
  worker (unit 23) feeds it the `patch` + content fetched from GitHub. Exercised live in the Phase-7 smoke
  (the changed `absolute` function correctly selected from PR #1's diff).

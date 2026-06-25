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
"""Phase 5: parse a Kestrel ``SandboxResult`` into structured bugs (no LLM; D39/D40).

``parse_results`` turns the raw output of a sandbox run — pytest's exit code plus its
stdout/stderr — into a ``BugReport``: which property tests failed, on what input, and how
badly. It reads two markers pytest and Hypothesis emit, scraping the text directly rather
than running a plugin inside the sandbox (D39):

* pytest's short-summary line  ``FAILED main.py::test_x - IndexError: ...`` — the
    authoritative set of failed tests plus the message each failed with.
* Hypothesis's falsifying line  ``Falsifying example: test_x(v='')`` — the exact input
    that broke the property (read with a balanced-bracket scan so multi-line / nested args
    survive).

Each failed ``test_<property_class>`` is mapped back to the detected property's testable
``relation`` (so ``violated_property`` is the relation, not just a class name). Severity is
two-way (D40): an ``AssertionError`` (an asserted relation failed = silent wrong answer) is a
``property_violation``; any other uncaught exception is a ``crash``. A timed-out run, or one
with no failures, yields no bugs (the caller maps ``timed_out`` to 504).
"""

from __future__ import annotations

import re

from .kestrel import SandboxResult
from .models import Bug, BugReport, BugSeverity, PropertyAnalysis

# pytest short test summary: "FAILED <nodeid>::<test_name> - <message>" (the " - <message>"
# tail is absent when pytest can't summarise the crash). <message> is pytest's rewritten
# assertion ("assert 1 == 2") or a raised exception ("IndexError: ...").
_FAILED_RE = re.compile(
    r"^FAILED\s+\S+?::(?P<name>\w+)(?:\s+-\s+(?P<msg>.*))?$",
    re.MULTILINE,
)

# pytest's final tally line, e.g. "1 failed, 3 passed in 0.42s".
_PASSED_RE = re.compile(r"(\d+)\s+passed\b")
_FAILED_COUNT_RE = re.compile(r"(\d+)\s+failed\b")

_FALSIFYING = "Falsifying example: "
_OPENERS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = {")", "]", "}"}

# A missing dependency in the network-less sandbox is an environment limit, NOT a bug in the
# function — so an Import/ModuleNotFound failure is never reported as a finding (Phase 10, D61).
_IMPORT_ERRORS = frozenset({"ModuleNotFoundError", "ImportError"})


def _scan_balanced(text: str, open_idx: int) -> tuple[str, int]:
    """Return (inside-text, index-after-close) for the bracket group opening at ``open_idx``.

    Tracks nested (), [], {} and skips bracket characters inside '...' or "..." string
    literals (honouring backslash escapes), so an arg like ``s=')'`` doesn't throw off the
    count. If the group never closes (truncated output), returns the remaining text.
    """
    depth = 0
    quote: str | None = None
    i, n = open_idx, len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : i], i + 1
        i += 1
    return text[open_idx + 1 :], n  # unbalanced (truncated) — take what we have


def _normalise_args(args: str) -> str:
    """Collapse a multi-line args capture to one tidy line, dropping a trailing comma.

    Real ``pytest -q`` renders Hypothesis's "Falsifying example:" block INSIDE the FAILURES
    traceback, so every line carries pytest's ``E   `` exception-line marker (e.g.
    ``E       x=-1,``). Strip that leading ``E``+whitespace prefix per line so it does not
    leak into the captured input; clean (un-prefixed) Hypothesis output is unaffected.
    """
    lines: list[str] = []
    for part in (p.strip() for p in args.splitlines()):
        if part == "E" or part.startswith("E "):
            part = part[1:].strip()
        lines.append(part)
    collapsed = re.sub(r"\s+", " ", " ".join(lines)).strip()
    return collapsed[:-1].rstrip() if collapsed.endswith(",") else collapsed


def _extract_falsifying(text: str) -> dict[str, str]:
    """Map test_name -> the args text of its first Hypothesis falsifying example."""
    examples: dict[str, str] = {}
    start = 0
    while (idx := text.find(_FALSIFYING, start)) != -1:
        cursor = idx + len(_FALSIFYING)
        paren = text.find("(", cursor)
        if paren == -1:
            break
        name = text[cursor:paren].strip()
        args, end = _scan_balanced(text, paren)
        start = end
        if name and name not in examples:
            examples[name] = _normalise_args(args)
    return examples


def _classify(msg: str | None) -> tuple[str, BugSeverity]:
    """Map a FAILED-summary message to (error_type, severity).

    pytest renders a rewritten assertion as ``assert <expr>`` (no type prefix) and a raised
    exception as ``<ExcType>: <msg>``. So a message starting with ``assert`` or
    ``AssertionError`` is a violated relation (``property_violation``, reported as
    ``"AssertionError"``); anything else is a ``crash`` whose type is the leading token.
    """
    msg = (msg or "").strip()
    if msg.startswith("assert") or msg.startswith("AssertionError"):
        return "AssertionError", BugSeverity.PROPERTY_VIOLATION
    return (msg.split(":", 1)[0].strip() or "Error"), BugSeverity.CRASH


def _relation_for(test_name: str, analysis: PropertyAnalysis) -> str:
    """Map a ``test_<property_class>[_n]`` name back to its detected relation.

    Strips the ``test_`` prefix and any ``_<n>`` repeat suffix to recover the property class,
    then returns the n-th detected property of that class's relation. Falls back to the bare
    class name, then the test name, when there's no detected match.
    """
    stem = test_name[len("test_") :] if test_name.startswith("test_") else test_name
    index = 0
    if (m := re.search(r"_(\d+)$", stem)):
        index = int(m.group(1)) - 1  # `_2` -> the 2nd detected property of that class
        stem = stem[: m.start()]
    matches = [p for p in analysis.detected if p.property_class.value == stem]
    if matches:
        return (matches[index] if 0 <= index < len(matches) else matches[0]).relation
    return stem or test_name


def parse_results(result: SandboxResult, analysis: PropertyAnalysis) -> BugReport:
    """Turn a sandbox run into a ``BugReport`` (which property tests failed, on what input)."""
    truncated = result.stdout_truncated or result.stderr_truncated
    text = result.stdout + "\n" + result.stderr

    if result.timed_out:
        return BugReport(timed_out=True, exit_code=result.exit_code, output_truncated=truncated)

    passed = int(m.group(1)) if (m := _PASSED_RE.search(text)) else 0
    failed = int(m.group(1)) if (m := _FAILED_COUNT_RE.search(text)) else 0

    if result.exit_code == 0:
        return BugReport(exit_code=0, tests_passed=passed, output_truncated=truncated)

    falsifying = _extract_falsifying(text)
    bugs: list[Bug] = []
    seen: set[str] = set()
    for match in _FAILED_RE.finditer(text):
        name = match.group("name")
        if name in seen:
            continue
        seen.add(name)
        error, severity = _classify(match.group("msg"))
        if error in _IMPORT_ERRORS:
            continue  # a missing sandbox dependency is not a function bug (D61)
        bugs.append(
            Bug(
                test_name=name,
                failing_input=falsifying.get(name, ""),
                error=error,
                violated_property=_relation_for(name, analysis),
                severity=severity,
            )
        )

    # Safety net: tests failed but the short summary was absent (a non-default pytest setup).
    # Build best-effort bugs from the falsifying examples we do have, defaulting to the common
    # property_violation case (we can't read the exception type without the summary).
    if not bugs and falsifying:
        bugs = [
            Bug(
                test_name=name,
                failing_input=finput,
                error="AssertionError",
                violated_property=_relation_for(name, analysis),
                severity=BugSeverity.PROPERTY_VIOLATION,
            )
            for name, finput in falsifying.items()
        ]

    return BugReport(
        bugs=bugs,
        exit_code=result.exit_code,
        tests_passed=passed,
        tests_failed=failed or len(bugs),
        output_truncated=truncated,
    )
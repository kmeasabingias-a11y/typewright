# 15 — `src/typewright/results.py`

## What this file is for

This file is the **detective that reads the crime-scene report**.

Unit 13 (`kestrel.py`) sent our generated tests into the sandbox and got back a `SandboxResult`
— but that result is *raw*: a big blob of text (everything pytest printed) plus an exit number.
Buried in that text is the thing we actually care about: *which property tests failed, and on
what input*. `results.py` is the part that reads through that blob and pulls out the structured
answer — a tidy list of **bugs**.

Think of it like a smoke detector that just went off and a fire report that's a long, messy
transcript of everything that happened. `results.py` is the investigator who reads the
transcript and writes the one-page summary: "Room 3, electrical fault, started at 2:14." It
turns noise into facts.

This is **Step 6** of the pipeline (in `PROJECT_BRIEF.md` §3): *Result parsing — no AI involved.*
No language model here; just careful, ordinary text reading.

---

## A mental model: two reliable signposts in the noise

pytest prints a *lot*. We don't try to understand all of it. Instead we look for exactly **two
signposts** that pytest and Hypothesis always plant in their output, and ignore everything else.

**Signpost 1 — the failure summary line.** Near the bottom, pytest lists each failed test on its
own line:

```
FAILED main.py::test_idempotence - assert 'AA' == 'A'
```

That single line tells us two things: *which* test failed (`test_idempotence`) and *what went
wrong* (the message after the dash). This is our authoritative "who failed" list.

**Signpost 2 — the falsifying example.** When a Hypothesis property test fails, Hypothesis prints
the exact input that broke it:

```
Falsifying example: test_idempotence(x='A')
```

That tells us the *input* — `x='A'` — the smallest value Hypothesis found that triggers the bug.

So the whole strategy is: find every Signpost 1 (the failed tests + why), find every Signpost 2
(the inputs), and **join them by test name**. That's it. We deliberately chose this
text-reading approach (decision **D39**) over a fancier one, because these two signposts have
looked the same for years and reading them needs no extra machinery inside the sandbox.

One more idea worth holding: **two kinds of failure mean two kinds of bug** (decision **D40**).

- If the test failed because an `assert` was false, the function gave a **wrong answer** — we
  call that a `property_violation`. This is the valuable kind: a silent bug that doesn't crash.
- If the test failed because the function *threw an exception* (an `IndexError`, say), we call
  that a `crash`.

Telling them apart matters, and there's a subtlety pytest forces on us — covered below.

---

## The whole file

```python
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

_FAILED_RE = re.compile(
    r"^FAILED\s+\S+?::(?P<name>\w+)(?:\s+-\s+(?P<msg>.*))?$",
    re.MULTILINE,
)

_PASSED_RE = re.compile(r"(\d+)\s+passed\b")
_FAILED_COUNT_RE = re.compile(r"(\d+)\s+failed\b")

_FALSIFYING = "Falsifying example: "
_OPENERS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = {")", "]", "}"}


def _scan_balanced(text: str, open_idx: int) -> tuple[str, int]:
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
    return text[open_idx + 1 :], n


def _normalise_args(args: str) -> str:
    collapsed = re.sub(r"\s+", " ", " ".join(p.strip() for p in args.splitlines())).strip()
    return collapsed[:-1].rstrip() if collapsed.endswith(",") else collapsed


def _extract_falsifying(text: str) -> dict[str, str]:
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
    msg = (msg or "").strip()
    if msg.startswith("assert") or msg.startswith("AssertionError"):
        return "AssertionError", BugSeverity.PROPERTY_VIOLATION
    return (msg.split(":", 1)[0].strip() or "Error"), BugSeverity.CRASH


def _relation_for(test_name: str, analysis: PropertyAnalysis) -> str:
    stem = test_name[len("test_") :] if test_name.startswith("test_") else test_name
    index = 0
    if (m := re.search(r"_(\d+)$", stem)):
        index = int(m.group(1)) - 1
        stem = stem[: m.start()]
    matches = [p for p in analysis.detected if p.property_class.value == stem]
    if matches:
        return (matches[index] if 0 <= index < len(matches) else matches[0]).relation
    return stem or test_name


def parse_results(result: SandboxResult, analysis: PropertyAnalysis) -> BugReport:
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
        bugs.append(
            Bug(
                test_name=name,
                failing_input=falsifying.get(name, ""),
                error=error,
                violated_property=_relation_for(name, analysis),
                severity=severity,
            )
        )

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
```

---

## Step-by-step

### The patterns at the top

```python
_FAILED_RE = re.compile(r"^FAILED\s+\S+?::(?P<name>\w+)(?:\s+-\s+(?P<msg>.*))?$", re.MULTILINE)
```

A **regular expression** (regex) is a search pattern for text. This one finds Signpost 1 lines.
In plain terms it reads: *a line that starts with `FAILED`, then some path, then `::`, then a
test name (captured as `name`), then optionally ` - ` and a message (captured as `msg`).* The
`re.MULTILINE` flag makes `^` and `$` mean "start/end of a line" rather than of the whole blob,
so it finds every such line. `_PASSED_RE` / `_FAILED_COUNT_RE` pick the numbers out of pytest's
final tally ("1 failed, 3 passed in 0.4s").

### `_scan_balanced` — reading an example without tripping over brackets

The failing input lives between the parentheses of `test_x(...)`. You might think "just grab
everything up to the next `)`" — but that breaks the moment a value *contains* a bracket, like
`s=')'`. So instead we **count brackets**. Starting at the opening `(`, we walk character by
character: every `(`, `[`, `{` adds one to a depth counter, every `)`, `]`, `}` subtracts one,
and when the depth returns to zero we've found the *matching* close. Crucially, while we're
inside a quoted string (`'...'` or `"..."`) we ignore brackets entirely — because a `)` inside
text is just a character, not a real closing bracket. That's the same care a code editor takes
to highlight matching brackets. If the output was cut off mid-example (truncated), we just take
what we have.

### `_extract_falsifying` — collect every input

This walks the whole blob finding each `Falsifying example: ` marker, reads the test name up to
its `(`, uses `_scan_balanced` to grab the arguments, tidies them to a single line with
`_normalise_args` (multi-line examples get collapsed; a trailing comma is dropped), and builds a
map of `test_name → input`. We keep the *first* example per test (Hypothesis reports the
simplest one).

### `_classify` — wrong answer, or crash?

```python
def _classify(msg):
    if msg.startswith("assert") or msg.startswith("AssertionError"):
        return "AssertionError", BugSeverity.PROPERTY_VIOLATION
    return (msg.split(":", 1)[0].strip() or "Error"), BugSeverity.CRASH
```

Here's the subtlety promised earlier. You'd expect a failed assertion to show as
`AssertionError: ...`. But pytest *rewrites* a bare `assert a == b` and prints it as plain
`assert 'AA' == 'A'` — **with no `AssertionError:` label at all**. So if we only looked at the
first word, we'd see `assert` (or `IndexError`, or whatever) and could easily mislabel the most
common case. The rule we use: if the message *starts with* `assert` or `AssertionError`, it's a
**property_violation** (a relation failed → wrong answer). Otherwise it's a **crash**, and the
exception's name is the bit before the first colon (e.g. `IndexError`).

### `_relation_for` — naming *which* property broke

Our generated tests are named after the property they check: `test_idempotence`,
`test_round_trip`, `test_metamorphic_2`. This function turns that name back into the actual
*relation* the property stood for (e.g. `normalize(normalize(x)) == normalize(x)`), by stripping
the `test_` prefix, peeling off any `_2`/`_3` repeat number, and looking up the matching detected
property from Phase 2. So a bug report says *which rule* the function violated, not just a label.
If it can't find a match, it falls back gracefully to the class name.

### `parse_results` — putting it together

The top-level function, read in order:

1. **Timed out?** Return a `BugReport` with `timed_out=True` and no bugs. (A timeout isn't a bug
   in the function — it's "we ran out of time"; the API turns this into a 504 later.)
2. **Read the tally** for the passed/failed counts.
3. **All passed (`exit_code == 0`)?** No bugs — return an empty report. 
4. **Otherwise**, gather the falsifying inputs, then walk every Signpost 1 line: for each failed
   test, build a `Bug` with its input (looked up by name), its error type and severity (from
   `_classify`), and the relation it violated (from `_relation_for`). A `seen` set avoids
   double-counting.
5. **Safety net:** if somehow no summary lines were found but we *do* have falsifying examples
   (a non-standard pytest setup), build best-effort bugs from those rather than reporting "no
   bugs" on a run that clearly failed.

It returns a `BugReport` — the bugs plus the run's outcome (counts, timeout, whether the output
was truncated). The API will surface just the `bugs` list as `bugs_found` in the next unit.

---

## What could go wrong

### 1. Mislabelling a wrong-answer as a crash
The whole point of TypeWright (decision D23) is catching *silent wrong answers*, not just
crashes. Because pytest prints bare assertions without an `AssertionError:` label, a naive parser
keying on the first word would tag those valuable property-violation bugs as generic crashes.
`_classify` checks for the `assert` prefix specifically to get this right.

### 2. Choking on brackets inside an input value
`Falsifying example: test_f(s=')')` would defeat a "find the next `)`" approach — it'd stop at
the `)` *inside* the string and capture a broken half. The bracket-counting scan (which ignores
brackets inside quotes) is what makes the captured input correct.

### 3. Treating a timeout as "no bugs found"
A timed-out run tells us nothing about whether the function is correct — only that the tests
didn't finish. Reporting "0 bugs" would be a dangerous false all-clear. We flag `timed_out`
separately and leave the bug list empty, so the API can say "exceeded time budget" (504) instead
of "looks good".

### 4. Trusting truncated output
Kestrel caps how much text it returns. If the report was cut off, a failing example might be
missing. We carry `output_truncated` on the report so the caller knows the picture might be
incomplete, rather than silently trusting a half-read transcript.

### 5. Depending on exact pytest wording
Text-scraping is only as stable as the text. We deliberately anchor on the two *most* stable
markers (`FAILED …` and `Falsifying example:`), which have held for years — and we recorded the
fallback plan (D39): if a future pytest/Hypothesis changes them, switch to a self-owned plugin
that emits structured JSON. For now, simple and stable wins.

---

## Summary

`results.py` is Step 6 of the pipeline: it reads the raw `SandboxResult` from a test run and
produces a `BugReport`. With no AI, it finds two stable signposts in pytest's output — the
`FAILED …` summary lines (who failed + why) and Hypothesis's `Falsifying example:` blocks (the
input) — joins them by test name, and for each failure records the input, the exception/error,
the relation that broke, and a two-way severity: `property_violation` (a wrong answer) versus
`crash` (an exception). It handles the awkward real-world details — bare-assert messages,
brackets inside values, multi-line examples, timeouts, and truncated output — so the bug list it
hands back is trustworthy. The next unit wires this into `/v1/analyze` so the API finally returns
`bugs_found`.

---

## Change history

- **2026-06-19** — Created in Phase 5, Unit 2. Holds `parse_results(result, analysis) -> BugReport`
  plus helpers: `_extract_falsifying` / `_scan_balanced` / `_normalise_args` (read the failing
  input, bracket-aware), `_classify` (message → error type + severity, handling pytest's
  prefix-less bare `assert`), and `_relation_for` (`test_<class>[_n]` → the detected relation).
  Text-scraping the stable `FAILED …` and `Falsifying example:` markers (D39); two-way severity
  `crash` vs `property_violation` (D40). No `/v1/analyze` wiring yet (standalone-first). Suite
  green at 67 passed.
- **2026-06-24** — Bugfix found by the **first live end-to-end smoke** (Phase 5 complete). Real
  `pytest -q` renders Hypothesis's `Falsifying example:` block *inside* the FAILURES traceback, so
  every line carries pytest's `E   ` exception-line marker (e.g. `E       x=-1,`). `_normalise_args`
  left those in, so `failing_input` came back as `"E x=-1, E"` instead of `x=-1`. Fix: strip a
  leading `E`+whitespace marker per line in `_normalise_args` (clean, un-prefixed output is
  unaffected). The Unit-2 transcript tests were hand-written without the prefix, so they missed it
  — this is exactly the "depending on exact pytest wording" risk above (§5), now covered by a
  regression test (`SAMPLE_ASSERT_EPREFIX`) built from the real layout. Smoke re-run confirms clean
  `x=-1` / `x=1`. Suite green at 73 passed.
- **2026-06-30** — Phase 10 (D61): a per-test failure whose error is `ModuleNotFoundError`/`ImportError`
  (`_IMPORT_ERRORS`) is **skipped** — never added to `bugs_found`. A missing dependency in the network-less
  sandbox is an environment limit, not a function bug (it was false-confirming as a phantom crash). The route
  also avoids the situation up front by skipping the run when a needed package is unavailable (walkthrough 06).

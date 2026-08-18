# 21 — `src/typewright/comment.py`

## What this file is for

This file writes the **PR comment** — the human-facing payoff of the whole bot. After the worker has
analyzed each changed function (found bugs, maybe a verified fix), this file turns those results into
one tidy piece of Markdown that GitHub will render nicely on the pull request.

It's pure formatting: results in, a string out. The worker posts it (unit 23); this file never touches
GitHub.

It exposes one function: `format_comment(findings)` → a Markdown string.

---

## A mental model: a short report, grouped by function

Picture the comment as a one-page report. It opens with a headline ("found N issues in M functions"),
then has a small section per function that had problems. Each section lists the bugs as bullets — for
each one: how bad it is (a wrong answer vs. an outright crash), which property it broke, and the exact
input that broke it. If we have a fix we *proved* works, it's tucked into a collapsible block so the
comment stays scannable, always with a "this is an AI suggestion, review it" label.

If nothing was wrong, the report is just a one-liner ("analyzed N functions, found no problems").

The input to all this is a list of `FunctionFinding` objects (unit 03) — each is one function's name,
its list of bugs, and an optional fix suggestion.

---

## The whole file

```python
"""Phase 7: render per-function analysis findings into one markdown PR comment (pure).

``format_comment`` takes the worker's per-function results (each a ``FunctionFinding``: the
function name, the bugs found, and an optional verified fix) and returns a single markdown body
to post on the PR. No I/O — the worker posts it (Unit 5).

The comment leads with a one-line summary, then a section per function with bugs: each bug names
the violated property/relation, the exact failing input, and the severity; a verified fix is
shown in a collapsible code block, always carrying the "AI suggestion — review carefully" label
the model attached (PROJECT_BRIEF §3 Step 7). A clean run yields a short "no issues" body.
"""

from __future__ import annotations

from .models import BugSeverity, FunctionFinding

_SEVERITY_LABEL = {
    BugSeverity.CRASH: "💥 crash",
    BugSeverity.PROPERTY_VIOLATION: "⚠️ wrong result",
}


def _format_bug(bug) -> str:
    sev = _SEVERITY_LABEL.get(bug.severity, bug.severity.value)
    where = f" on `{bug.failing_input}`" if bug.failing_input else ""
    return f"- **{sev}** — `{bug.violated_property}` fails{where} ({bug.error})"


def _format_finding(finding) -> str:
    lines = [f"### `{finding.function_name}`", "", f"**{len(finding.bugs)} issue(s) found:**", ""]
    lines += [_format_bug(b) for b in finding.bugs]
    lines.append("")
    fix = finding.fix_suggestion
    if fix is not None:
        status = "verified — re-ran the tests green" if fix.verified else "UNVERIFIED — tests still fail"
        lines += [
            f"<details><summary>Suggested fix ({status})</summary>",
            "",
            "```python",
            fix.code.strip(),
            "```",
        ]
        if fix.explanation:
            lines.append(f"_{fix.explanation}_")
        lines += ["", f"> {fix.disclaimer}", "</details>", ""]
    return "\n".join(lines)


def format_comment(findings: list[FunctionFinding]) -> str:
    """Render all findings into a single markdown comment body."""
    with_bugs = [f for f in findings if f.bugs]
    if not with_bugs:
        return (
            f"## ✅ TypeWright\n\nAnalyzed {len(findings)} changed function(s) and found no "
            f"property violations."
        )
    total = sum(len(f.bugs) for f in with_bugs)
    header = f"## 🔍 TypeWright found {total} issue(s) in {len(with_bugs)} function(s)\n"
    return header + "\n" + "\n".join(_format_finding(f) for f in with_bugs)
```

---

## Step-by-step

### `_format_bug(bug)`
One bullet per bug: a severity label (`💥 crash` for a thrown exception, `⚠️ wrong result` for a failed
property assertion — the silent-wrong-answer case this whole project is about), the violated property in
code formatting, the exact failing input (e.g. `x=-1`), and the error type in parentheses.

### `_format_finding(finding)`
One section per function: a `###` heading with the function name, a count of issues, the bug bullets, and
— if there's a fix — a collapsible `<details>` block. The block's summary says whether the fix is
**verified** (re-ran the tests green) or **UNVERIFIED** (we proposed something but it didn't pass), then
shows the corrected code, the model's one-line explanation, and the standing "review carefully"
disclaimer. The `<details>` element keeps long fixes from cluttering the comment — readers expand it if
they want it.

### `format_comment(findings)`
- Keep only the findings that actually have bugs.
- If none do → a short, friendly "no property violations" line (so a clean run reads clearly if the
  worker chooses to post it).
- Otherwise → a headline counting total issues and affected functions, then each function's section
  joined together.

---

## What could go wrong

### 1. A wall of text
Dumping every fix inline would make the comment huge and hard to skim. The collapsible `<details>` block
keeps the bugs visible and the fixes one click away.

### 2. Passing off an unproven fix as proven
The summary text is explicit: `verified — re-ran the tests green` vs `UNVERIFIED — tests still fail`. A
reader never has to guess whether the fix was actually checked.

### 3. Hiding that a suggestion is machine-made
Every fix carries the `disclaimer` ("AI suggestion — review carefully before applying"). That's a
transparency/safety label required by the project brief (§3 Step 7) — distinct from authorship credit;
it's there so a human always knows to review before applying.

### 4. Losing the failing input
The single most useful thing in a property-test result is the exact input that broke the code (`x=-1`).
Each bullet leads with the property and that input, so the developer can reproduce it immediately.

---

## Summary

`comment.py` renders the worker's `FunctionFinding`s into one Markdown comment: a headline, a section per
buggy function (severity + violated property + failing input per bug), and an optional collapsible
**verified/unverified** fix block carrying the "AI suggestion" disclaimer — or a one-line "no issues"
message when clean. Pure formatting; the worker posts it. Part of decision **D48**.

---

## Change history

- **2026-06-25** — Created in Phase 7, Unit 4 (D48). `format_comment(list[FunctionFinding]) -> str`:
  headline + per-function bug bullets (severity label, violated property, failing input) + collapsible
  verified/unverified fix block with the model's disclaimer; short "no issues" body when clean. Pure; new
  `FunctionFinding` model (unit 03). Rendered cleanly on the real PR in the Phase-7 live smoke.
- **2026-06-28** — D57: when bugs are shown, the comment now ends with an **inferred-property disclaimer**
  ("findings are violations of AI-inferred properties — confirm each is one the function is meant to
  guarantee"). Honest framing for the §8-risk-2 over-inference case (which confidence-gating can't filter —
  the `slugify` phantom came back at 0.90 confidence). No disclaimer on a clean "no issues" body.

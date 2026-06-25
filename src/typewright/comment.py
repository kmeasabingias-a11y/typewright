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
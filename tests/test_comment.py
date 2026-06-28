"""Tests for the PR comment formatter (Phase 7), pure."""

from typewright.comment import format_comment
from typewright.models import Bug, BugSeverity, FixSuggestion, FunctionFinding


def _bug(**kw):
    defaults = dict(
        test_name="test_value_postcondition",
        failing_input="x=-1",
        error="AssertionError",
        violated_property="absolute(x) >= 0",
        severity=BugSeverity.PROPERTY_VIOLATION,
    )
    defaults.update(kw)
    return Bug(**defaults)


def test_clean_run_says_no_issues():
    out = format_comment([FunctionFinding(function_name="ok", bugs=[])])
    assert "no property violations" in out
    assert "TypeWright" in out


def test_bug_and_verified_fix_rendered():
    fix = FixSuggestion(
        code="def absolute(x):\n    return x if x >= 0 else -x",
        explanation="guards negatives",
        verified=True,
        tests_passed=3,
    )
    out = format_comment(
        [FunctionFinding(function_name="absolute", bugs=[_bug()], fix_suggestion=fix)]
    )
    assert "found 1 issue" in out
    assert "`absolute`" in out
    assert "x=-1" in out
    assert "absolute(x) >= 0" in out
    assert "return x if x >= 0 else -x" in out
    assert "verified" in out
    assert "review carefully" in out.lower()


def test_unverified_fix_is_flagged():
    fix = FixSuggestion(code="def f(): ...", explanation="x", verified=False)
    out = format_comment([FunctionFinding(function_name="f", bugs=[_bug()], fix_suggestion=fix)])
    assert "UNVERIFIED" in out


def test_bug_without_fix_has_no_fix_section():
    out = format_comment([FunctionFinding(function_name="f", bugs=[_bug()], fix_suggestion=None)])
    assert "Suggested fix" not in out


def test_crash_severity_label():
    out = format_comment(
        [FunctionFinding(function_name="f", bugs=[_bug(severity=BugSeverity.CRASH, error="IndexError")])]
    )
    assert "crash" in out.lower()
    assert "IndexError" in out


def test_multiple_functions_counted():
    out = format_comment(
        [
            FunctionFinding(function_name="a", bugs=[_bug()]),
            FunctionFinding(function_name="b", bugs=[_bug(), _bug()]),
        ]
    )
    assert "found 3 issue(s) in 2 function(s)" in out


def test_comment_includes_inferred_property_disclaimer():
    from typewright.comment import format_comment
    from typewright.models import Bug, BugSeverity, FunctionFinding

    finding = FunctionFinding(
        function_name="f",
        bugs=[Bug(test_name="test_x", failing_input="x=1", error="AssertionError",
                violated_property="f(x) == x", severity=BugSeverity.PROPERTY_VIOLATION)],
    )
    assert "AI-inferred" in format_comment([finding])
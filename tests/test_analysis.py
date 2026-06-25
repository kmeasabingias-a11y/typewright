"""Tests for analyze_one — the per-function pipeline the worker runs (Phase 7). Steps mocked."""

from typewright import analysis
from typewright.config import Settings
from typewright.kestrel import SandboxResult
from typewright.models import (
    Argument,
    Bug,
    BugReport,
    BugSeverity,
    FunctionMetadata,
    GeneratedTestFile,
    ProposedFix,
    PropertyAnalysis,
    StrategyPlan,
)


def _meta():
    return FunctionMetadata(
        name="absolute",
        args=[Argument(name="x", type_hint="int")],
        return_type="int",
        signature="absolute(x: int) -> int",
        source="def absolute(x: int) -> int:\n    return x\n",
    )


def _bug():
    return Bug(
        test_name="t",
        failing_input="x=-1",
        error="AssertionError",
        violated_property="absolute(x) >= 0",
        severity=BugSeverity.PROPERTY_VIOLATION,
    )


def _patch_chain(monkeypatch, *reports, proposed=None):
    monkeypatch.setattr(analysis, "infer_properties", lambda meta, s: PropertyAnalysis())
    monkeypatch.setattr(analysis, "generate_strategies", lambda meta, props, s: StrategyPlan())
    monkeypatch.setattr(analysis, "generate_test_file", lambda meta, props, plan, s: GeneratedTestFile(source="x"))
    monkeypatch.setattr(analysis, "run_tests", lambda tf, *, timeout_seconds, settings: SandboxResult("", "", 0, 1, False))
    staged = list(reports)
    monkeypatch.setattr(analysis, "parse_results", lambda res, props: staged.pop(0))
    if proposed is not None:
        monkeypatch.setattr(analysis, "suggest_fix", lambda meta, rep, s: proposed)
        monkeypatch.setattr(analysis, "build_fix_file", lambda tf, meta, fix: GeneratedTestFile(source="y"))


def test_analyze_one_returns_bugs_and_verified_fix(monkeypatch):
    report = BugReport(bugs=[_bug()], exit_code=1, tests_failed=1)
    verify = BugReport(bugs=[], exit_code=0, tests_passed=3)
    proposed = ProposedFix(corrected_source="def absolute(x):\n    return abs(x)", explanation="fix")
    _patch_chain(monkeypatch, report, verify, proposed=proposed)

    finding = analysis.analyze_one(_meta(), Settings(_env_file=None))
    assert finding.function_name == "absolute"
    assert len(finding.bugs) == 1
    assert finding.fix_suggestion is not None and finding.fix_suggestion.verified is True


def test_analyze_one_clean_has_no_bugs_no_fix(monkeypatch):
    _patch_chain(monkeypatch, BugReport(bugs=[], exit_code=0, tests_passed=5))
    finding = analysis.analyze_one(_meta(), Settings(_env_file=None))
    assert finding.bugs == [] and finding.fix_suggestion is None
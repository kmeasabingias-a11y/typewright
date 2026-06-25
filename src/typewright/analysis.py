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
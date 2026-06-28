"""Tests for the SQLite run store (Phase 8, Unit 2a, D50)."""

from typewright.models import (
    AnalyzedFunction,
    AnalyzeResponse,
    GeneratedTestFile,
    PropertyAnalysis,
    StrategyPlan,
)
from typewright.store import SqliteRunStore


def _sample_response(analysis_id: str = "abc-123") -> AnalyzeResponse:
    return AnalyzeResponse(
        analysis_id=analysis_id,
        function=AnalyzedFunction(name="f", signature="(x)", args=[]),
        properties=PropertyAnalysis(),
        strategy_plan=StrategyPlan(),
        test_file=GeneratedTestFile(source="def f(x):\n    return x", test_names=[], skipped=[]),
        bugs_found=[],
        fix_suggestion=None,
    )


def test_save_then_load_round_trips(tmp_path):
    store = SqliteRunStore(str(tmp_path / "runs.db"))
    resp = _sample_response()
    store.save(resp)
    assert store.load("abc-123") == resp


def test_load_unknown_returns_none(tmp_path):
    store = SqliteRunStore(str(tmp_path / "runs.db"))
    assert store.load("nope") is None


def test_save_is_idempotent_and_durable(tmp_path):
    path = str(tmp_path / "runs.db")
    store = SqliteRunStore(path)
    store.save(_sample_response())
    store.save(_sample_response())  # same id again -> INSERT OR REPLACE, no error
    # a second store opened on the same file sees the row (durability across instances)
    assert SqliteRunStore(path).load("abc-123") is not None
"""Tests for per-analysis tracing (Phase 9, Unit 4, D54)."""

import json
import logging

from typewright.logging_config import _JsonFormatter
from typewright.tracing import span, trace_scope


def test_spans_are_recorded_on_the_trace():
    with trace_scope("t1", function="f") as trace:
        with span("detection"):
            pass
        with span("sandbox"):
            pass
    assert [s.name for s in trace.spans] == ["detection", "sandbox"]
    assert trace.duration_ms >= 0
    assert all(s.duration_ms >= 0 for s in trace.spans)


def test_trace_set_merges_attrs():
    with trace_scope("t2") as trace:
        trace.set(bugs=3, fix_verified=True)
    assert trace.attrs["bugs"] == 3
    assert trace.attrs["fix_verified"] is True


def test_span_outside_a_trace_is_a_noop():
    with span("orphan"):  # no active trace -> must not raise
        pass


def test_trace_scope_emits_a_summary(caplog):
    with caplog.at_level(logging.INFO, logger="typewright"):
        with trace_scope("t3", function="absolute") as trace:
            with span("detection"):
                pass
            trace.set(bugs=2)
    assert any(
        "event=analysis_trace" in m and "trace=t3" in m and "bugs=2" in m
        for m in (r.getMessage() for r in caplog.records)
    )


def test_json_formatter_emits_valid_json():
    record = logging.LogRecord("typewright", logging.INFO, __file__, 1, "hello", None, None)
    record.trace = {"event": "analysis_trace", "trace": "t4", "bugs": 1}
    obj = json.loads(_JsonFormatter().format(record))
    assert obj["message"] == "hello"
    assert obj["trace"] == "t4"
    assert obj["bugs"] == 1
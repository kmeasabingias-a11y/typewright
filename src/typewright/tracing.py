"""Per-analysis tracing (Phase 9, Unit 4, D54).

Each analysis gets a request-scoped ``Trace`` bound by a contextvar (the same shape as the cost
meter). The /v1/analyze route opens ``trace_scope(analysis_id, ...)`` around the pipeline and wraps
each stage in ``span(name)``, which times the block and records ``(name, duration_ms)`` on the active
trace. On exit the trace emits ONE structured summary log line — the per-stage timeline plus the
outcome (cost, bugs, duration) — greppable as logfmt and, with ``log_format=json``, machine-parseable
for any aggregator. There is no external service; the emit is a single log call, so an external
backend (Langfuse, OpenTelemetry) is a drop-in later without touching the route.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from dataclasses import dataclass, field
from time import perf_counter
from typing import Iterator

logger = logging.getLogger("typewright")


@dataclass(frozen=True)
class Span:
    name: str
    duration_ms: int


@dataclass
class Trace:
    trace_id: str
    attrs: dict = field(default_factory=dict)
    spans: list[Span] = field(default_factory=list)
    duration_ms: int = 0

    def record(self, name: str, duration_ms: int) -> None:
        self.spans.append(Span(name, duration_ms))

    def set(self, **attrs) -> None:
        self.attrs.update(attrs)


_active_trace: contextvars.ContextVar["Trace | None"] = contextvars.ContextVar(
    "active_trace", default=None
)


def _fmt(value: object) -> str:
    """Render a value for the logfmt summary."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def _emit(trace: Trace) -> None:
    """Log one structured summary line for a completed (or failed) analysis."""
    fields: dict = {"event": "analysis_trace", "trace": trace.trace_id, **trace.attrs}
    for span_ in trace.spans:
        fields[f"{span_.name}_ms"] = span_.duration_ms
    fields["duration_ms"] = trace.duration_ms
    message = " ".join(f"{k}={_fmt(v)}" for k, v in fields.items())
    logger.info(message, extra={"trace": fields})


@contextlib.contextmanager
def trace_scope(trace_id: str, **attrs) -> Iterator[Trace]:
    """Bind a ``Trace`` to the current context for one analysis; emit its summary on exit."""
    trace = Trace(trace_id=trace_id, attrs=dict(attrs))
    token = _active_trace.set(trace)
    started = perf_counter()
    try:
        yield trace
    finally:
        trace.duration_ms = int((perf_counter() - started) * 1000)
        _active_trace.reset(token)
        _emit(trace)


@contextlib.contextmanager
def span(name: str) -> Iterator[None]:
    """Time a pipeline stage and record it on the active trace (a no-op outside a trace_scope)."""
    started = perf_counter()
    try:
        yield
    finally:
        trace = _active_trace.get()
        if trace is not None:
            trace.record(name, int((perf_counter() - started) * 1000))
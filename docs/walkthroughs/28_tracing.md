# 28 — `src/typewright/tracing.py` (a timeline for every analysis)

## 1. What this file is for

When an analysis takes 24 seconds, *where did the time go?* When it cost 1.2 cents, *which
step spent it?* This file answers that. For every analysis it records a little **timeline** —
how long each stage took (detect, strategy, test-gen, sandbox, fix) — and then writes **one
summary line** to the log with that timeline plus the outcome (how many bugs, total cost,
total time). That's "tracing": a per-request story you can read back later.

The neat part is *how* it stays out of the way. The stages live in five different functions;
we don't want to pass a stopwatch into all of them. So, exactly like the cost meter
(`metrics.py`), it uses a **per-request scratchpad** that the timing helper finds on its own.

Analogy: a race with split timers. The route starts a stopwatch for the whole race
(`trace_scope`), and each lap (`span`) records its own split. At the finish line, one line is
posted with every split and the final result. No runner carries the clipboard.

## 2. A mental model

1. **A `Trace` is the scorecard.** It holds the analysis id, a list of `Span`s (each a stage
   name + how many milliseconds it took), some attributes (function name, bug count, cost…), and
   the total duration.

2. **A `contextvar` is "the scorecard that's open right now."** Like the cost meter, the trace is
   bound to the current request's context, so concurrent analyses each keep their own — no mixing.

3. **`span(name)` is a self-timing lap.** Wrap a stage in `with span("sandbox"):` and it records
   how long that block took onto whatever trace is currently open. If none is open (a background
   job, a unit test), it quietly does nothing.

4. **The summary is just a log line.** On the way out, `trace_scope` writes one structured line.
   No database, no external service. With `log_format=json` that line is machine-readable for a log
   aggregator; in plain text it's still human-greppable. And because it's *just a log call*, a fancy
   backend (Langfuse, OpenTelemetry) could be slotted in later without touching the route.

## 3. The whole file

```python
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
```

## 4. Step-by-step

**`Span` / `Trace`.** A `Span` is one stage's name + milliseconds. A `Trace` collects them, plus a
free-form `attrs` dict (function, bugs, cost…) and the overall `duration_ms`. `record` adds a span;
`set(**attrs)` merges in outcome fields.

**`_active_trace`.** The contextvar holding the open trace, default `None`. Per request, so parallel
analyses don't share one.

**`_fmt` / `_emit`.** `_emit` builds the summary: it merges the trace id, the attrs, one `{stage}_ms`
per span, and the total, then logs it as a `key=value` (logfmt) message **and** passes the same fields
as structured `extra={"trace": …}` — so a plain-text log is readable and a JSON log (see
`02_logging_config.md`) carries the fields as real JSON. `_fmt` keeps booleans as `true/false` and
trims float noise.

**`trace_scope`.** Creates the trace, makes it the open one, starts the clock, and `yield`s it so the
route can `set(...)` outcome fields. In `finally` — always, even if the pipeline raised — it stamps the
total duration, closes the trace (resets the contextvar), and emits the summary. So a failed run still
logs a trace showing how far it got.

**`span`.** Times its block and, on exit, records the elapsed ms onto the open trace. Outside any
`trace_scope` it finds no trace and does nothing — which is why the GitHub worker and the unit tests
that don't open a scope are unaffected.

**How the route uses it (see `06_main.md`).** `/v1/analyze` opens
`trace_scope(analysis_id, model_tier=…)` alongside `cost_scope`, wraps each stage in `with span("…"):`,
and calls `trace.set(bugs=…, llm_cost_usd=…, …)` before the scope closes. The `analysis_id` is the
trace id, so the log line, the API response, and the shareable `?run=` link all share one identifier.

## 5. What could go wrong (and why the code is shaped to avoid it)

- **Mixing two analyses' timings.** A plain global "current trace" would let concurrent requests record
  onto each other. The contextvar gives each request its own — same reasoning as the cost meter.
- **Losing the trace when a stage fails.** If `_emit` ran only on success, a crash (a 500, a 402 budget
  abort, a 504 timeout) would vanish silently. `trace_scope` emits in `finally`, so you still get a
  trace — with the stages that completed — for a failed run.
- **A leaked open trace.** As with the meter, `finally` resets the contextvar, so the next request on
  that thread starts clean.
- **Cluttering every step with timing code.** Threading a timer through the five stage functions would
  bury the analysis logic in bookkeeping. `span` reads the open trace itself, so the stages stay about
  detecting properties, not stopwatches.
- **Logs a machine can't read.** The summary is emitted both as a greppable logfmt message and as
  structured `extra` fields; flip `log_format=json` and an aggregator gets clean JSON without changing
  this file.

## 6. Change history

- **2026-06-28** — **Created (Phase 9, Unit 4, D54).** Per-analysis tracing: a `Trace`/`Span` recorded via a
  `trace_scope` contextvar + a `span(name)` stage timer (mirrors `metrics.cost_scope`). The `/v1/analyze`
  route wraps each stage in `span(...)` and emits one structured summary log per analysis (per-stage
  timeline + cost/bugs/duration, keyed by `analysis_id`). Backend-agnostic — just a log call, so
  Langfuse/OTel is a future drop-in; `log_format=json` (see `02_logging_config.md`) makes the fields
  machine-parseable. See `06_main.md` for the wiring.

"""Per-request cost + timing accounting for an analysis (Phase 9, Unit 1, D51).

LLM spend is a cross-cutting concern: rather than thread a cost accumulator through every
pipeline step, the /v1/analyze route opens a ``cost_scope()`` around the whole pipeline, which
binds a fresh ``CostMeter`` to the current context via a contextvar. The single LLM chokepoint
(``llm.complete``) calls ``add_cost(raw)`` after each completion; if a scope is active it adds
that completion's LiteLLM-computed cost to the meter. So detection, strategy, test-gen, and the
optional fix all accrue to one meter — which fills ``AnalyzeResponse.metadata.llm_cost_usd`` —
without touching any step's signature or its tests. Outside a scope (e.g. the GitHub worker, or
a unit test that doesn't open one) ``add_cost`` is a silent no-op.
"""

from __future__ import annotations

import contextlib
import contextvars
from typing import Iterator

import litellm


class CostMeter:
    """Accumulates the USD cost of the LLM calls made within one analysis."""

    def __init__(self) -> None:
        self.total_usd: float = 0.0
        self.calls: int = 0

    def add(self, cost_usd: float) -> None:
        self.total_usd += cost_usd
        self.calls += 1


_active_meter: contextvars.ContextVar[CostMeter | None] = contextvars.ContextVar(
    "active_cost_meter", default=None
)


def _response_cost(raw_completion: object) -> float:
    """Best-effort USD cost of one LiteLLM completion; 0.0 if it can't be determined.

    LiteLLM stashes the cost it computed on the response (``_hidden_params['response_cost']``);
    if that is absent we ask LiteLLM to compute it. A model missing from LiteLLM's price map (or
    any error) degrades to 0.0 rather than failing the analysis — cost is reported, never enforced
    here (the budget is Unit 2).
    """
    try:
        hidden = getattr(raw_completion, "_hidden_params", None) or {}
        cost = hidden.get("response_cost")
        if cost is None:
            cost = litellm.completion_cost(completion_response=raw_completion)
        return float(cost or 0.0)
    except Exception:
        return 0.0


def add_cost(raw_completion: object) -> None:
    """Add one completion's cost to the active analysis meter, if a ``cost_scope`` is open."""
    meter = _active_meter.get()
    if meter is not None:
        meter.add(_response_cost(raw_completion))


@contextlib.contextmanager
def cost_scope() -> Iterator[CostMeter]:
    """Bind a fresh ``CostMeter`` to the current context for one analysis; yield it to read later."""
    meter = CostMeter()
    token = _active_meter.set(meter)
    try:
        yield meter
    finally:
        _active_meter.reset(token)
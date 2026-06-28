"""Tests for per-request cost accounting (Phase 9, Unit 1, D51)."""

import pytest

from typewright import metrics
from typewright.errors import CostBudgetExceededError
from typewright.metrics import CostMeter, add_cost, cost_scope


class _RawWithHiddenCost:
    """Stand-in for a LiteLLM response carrying a precomputed cost."""

    def __init__(self, cost):
        self._hidden_params = {"response_cost": cost}


def test_cost_meter_accumulates():
    m = CostMeter()
    m.add(0.01)
    m.add(0.02)
    assert round(m.total_usd, 4) == 0.03
    assert m.calls == 2


def test_add_cost_within_scope_bills_the_meter():
    with cost_scope() as meter:
        add_cost(_RawWithHiddenCost(0.0123))
        add_cost(_RawWithHiddenCost(0.0077))
    assert round(meter.total_usd, 4) == 0.02
    assert meter.calls == 2


def test_add_cost_outside_scope_is_a_noop():
    add_cost(_RawWithHiddenCost(0.05))  # no active scope -> must not raise


def test_response_cost_degrades_to_zero_on_bad_input():
    assert metrics._response_cost(object()) == 0.0


def test_cost_meter_raises_when_over_budget():
    m = CostMeter(limit_usd=0.01)
    m.add(0.005)  # under the cap -> fine
    with pytest.raises(CostBudgetExceededError):
        m.add(0.01)  # total 0.015 > 0.01
    assert round(m.total_usd, 4) == 0.015


def test_cost_scope_enforces_the_budget():
    with pytest.raises(CostBudgetExceededError):
        with cost_scope(limit_usd=0.01) as meter:
            add_cost(_RawWithHiddenCost(0.02))
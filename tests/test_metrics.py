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


def test_monthly_meter_accumulates_and_persists(tmp_path):
    from typewright.metrics import MonthlyCostMeter

    db = str(tmp_path / "runs.db")
    m = MonthlyCostMeter(db, limit_usd=1.00)
    m.add(0.30)
    m.add(0.20)
    assert round(m.current_total(), 4) == 0.50
    # a fresh meter on the same file sees the same total -> durable across restarts
    assert round(MonthlyCostMeter(db, limit_usd=1.00).current_total(), 4) == 0.50


def test_monthly_meter_check_raises_when_exhausted(tmp_path):
    from typewright.errors import MonthlyBudgetExceededError
    from typewright.metrics import MonthlyCostMeter

    m = MonthlyCostMeter(str(tmp_path / "runs.db"), limit_usd=0.10)
    m.add(0.05)
    m.check()  # under the cap -> fine
    m.add(0.06)  # total 0.11 >= 0.10
    with pytest.raises(MonthlyBudgetExceededError) as excinfo:
        m.check()
    assert excinfo.value.limit_usd == 0.10
    assert excinfo.value.retry_after > 0


def test_monthly_meter_add_ignores_nonpositive(tmp_path):
    from typewright.metrics import MonthlyCostMeter

    m = MonthlyCostMeter(str(tmp_path / "runs.db"), limit_usd=1.00)
    m.add(0.0)
    m.add(-0.5)
    assert m.current_total() == 0.0


def test_monthly_meter_disabled_when_nonpositive():
    from types import SimpleNamespace
    from typewright.llm import _monthly_meter
    from typewright.metrics import MonthlyCostMeter

    disabled = SimpleNamespace(max_monthly_cost_usd=0, runs_db_path="unused.db")
    enabled = SimpleNamespace(max_monthly_cost_usd=5.0, runs_db_path="unused.db")
    assert _monthly_meter(disabled) is None
    assert isinstance(_monthly_meter(enabled), MonthlyCostMeter)
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


def test_daily_meter_is_a_separate_counter_from_the_monthly_one(tmp_path):
    """The daily cap (D62) keeps its own row/table, so the two ceilings are independent."""
    from typewright.metrics import DailyCostMeter, MonthlyCostMeter

    db = str(tmp_path / "runs.db")
    daily = DailyCostMeter(db, limit_usd=1.00)
    monthly = MonthlyCostMeter(db, limit_usd=10.00)
    daily.add(0.40)
    assert round(daily.current_total(), 4) == 0.40
    assert monthly.current_total() == 0.0  # billing one does not bill the other
    monthly.add(0.25)
    assert round(daily.current_total(), 4) == 0.40
    assert round(DailyCostMeter(db, limit_usd=1.00).current_total(), 4) == 0.40  # durable


def test_daily_meter_check_raises_with_a_daily_period_and_same_day_retry(tmp_path):
    """Exhausting the daily cap is the same 503 as monthly, labelled daily, clearing at midnight."""
    from typewright.errors import MonthlyBudgetExceededError
    from typewright.metrics import DailyCostMeter

    m = DailyCostMeter(str(tmp_path / "runs.db"), limit_usd=0.10)
    m.add(0.05)
    m.check()  # under the cap -> fine
    m.add(0.06)  # total 0.11 >= 0.10
    with pytest.raises(MonthlyBudgetExceededError) as excinfo:
        m.check()
    assert excinfo.value.period == "Daily"
    assert "Daily LLM-cost budget" in str(excinfo.value)
    # rollover is the next UTC midnight -> never more than a day away
    assert 0 < excinfo.value.retry_after <= 86_400


def test_budget_meters_include_only_the_caps_that_are_enabled():
    """A cap <= 0 is off; each positive cap contributes its own meter (D58 monthly, D62 daily)."""
    from types import SimpleNamespace
    from typewright.llm import _budget_meters
    from typewright.metrics import DailyCostMeter, MonthlyCostMeter

    def settings(monthly, daily):
        return SimpleNamespace(
            max_monthly_cost_usd=monthly, max_daily_cost_usd=daily, runs_db_path="unused.db"
        )

    assert _budget_meters(settings(0, 0)) == []
    assert [type(m) for m in _budget_meters(settings(5.0, 0))] == [MonthlyCostMeter]
    assert [type(m) for m in _budget_meters(settings(0, 1.0))] == [DailyCostMeter]
    assert [type(m) for m in _budget_meters(settings(5.0, 1.0))] == [
        MonthlyCostMeter,
        DailyCostMeter,
    ]
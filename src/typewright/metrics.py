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
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Iterator

import litellm

from .errors import CostBudgetExceededError, MonthlyBudgetExceededError


class CostMeter:
    """Accumulates the USD cost of the LLM calls made within one analysis."""

    def __init__(self, limit_usd: float | None = None) -> None:
        self.total_usd: float = 0.0
        self.calls: int = 0
        self.limit_usd: float | None = limit_usd

    def add(self, cost_usd: float) -> None:
        self.total_usd += cost_usd
        self.calls += 1
        if self.limit_usd is not None and self.total_usd > self.limit_usd:
            raise CostBudgetExceededError(self.total_usd, self.limit_usd)


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
def cost_scope(limit_usd: float | None = None) -> Iterator[CostMeter]:
    """Bind a fresh ``CostMeter`` (optionally budget-capped) to the context for one analysis."""
    meter = CostMeter(limit_usd=limit_usd)
    token = _active_meter.set(meter)
    try:
        yield meter
    finally:
        _active_meter.reset(token)


class PeriodCostMeter:
    """A durable, process-shared LLM-spend ceiling for one calendar period, persisted in SQLite.

    Unlike the per-analysis ``CostMeter`` (request-scoped, in-memory, D52), this caps the
    operator's *aggregate* spend across every analysis and entry point within a period. The
    running total lives in its own table in the same SQLite file as the run store
    (``runs_db_path``), keyed by the period (UTC), so the web process and the GitHub-App worker
    share one counter and it survives restarts. A connection is opened per call (WAL), mirroring
    ``SqliteRunStore``, so it is safe from FastAPI's threadpool.

    Enforcement is a PRE-check at the LLM chokepoint: ``check()`` runs before each completion and
    raises ``MonthlyBudgetExceededError`` (-> 503) once the period's total has reached the ceiling,
    so a request made *after* the cap is hit costs zero LLM calls. ``add()`` records spend after a
    completion and never raises (the call already happened); the next ``check()`` is the gate. Net
    effect, like D52: spend is bounded at the ceiling plus the calls in flight when it was crossed.

    Subclasses supply the table/column, the period key, and how long until the period rolls over.
    """

    table: str
    column: str
    period_label: str

    def __init__(self, db_path: str, limit_usd: float) -> None:
        self._path = db_path
        self.limit_usd = limit_usd

    # --- period definition (subclass hooks) ---
    @staticmethod
    def _period_key() -> str:
        raise NotImplementedError

    @staticmethod
    def _seconds_to_rollover() -> int:
        raise NotImplementedError

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {self.table} ("
            f"{self.column} TEXT PRIMARY KEY, total_usd REAL NOT NULL)"
        )
        return conn

    def current_total(self) -> float:
        """USD spent so far in the current (UTC) period."""
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT total_usd FROM {self.table} WHERE {self.column} = ?",
                (self._period_key(),),
            ).fetchone()
        finally:
            conn.close()
        return float(row[0]) if row is not None else 0.0

    def check(self) -> None:
        """Raise MonthlyBudgetExceededError (-> 503) if this period's spend has hit the ceiling."""
        total = self.current_total()
        if total >= self.limit_usd:
            raise MonthlyBudgetExceededError(
                total,
                self.limit_usd,
                self._seconds_to_rollover(),
                period=self.period_label,
            )

    def add(self, cost_usd: float) -> None:
        """Atomically add ``cost_usd`` to this period's running total (never raises)."""
        if cost_usd <= 0:
            return
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    f"INSERT INTO {self.table} ({self.column}, total_usd) VALUES (?, ?) "
                    f"ON CONFLICT({self.column}) DO UPDATE SET "
                    "total_usd = total_usd + excluded.total_usd",
                    (self._period_key(), cost_usd),
                )
        finally:
            conn.close()

    def add_from_raw(self, raw_completion: object) -> None:
        """Bill one LiteLLM completion's cost to the period total (best-effort, 0.0 on a miss)."""
        self.add(_response_cost(raw_completion))


class MonthlyCostMeter(PeriodCostMeter):
    """The global monthly LLM-spend ceiling (Phase 10, D58) — the operator's hard monthly bill."""

    table = "monthly_cost"
    column = "month"
    period_label = "Monthly"

    @staticmethod
    def _period_key() -> str:
        return _month_key()

    @staticmethod
    def _seconds_to_rollover() -> int:
        return _seconds_to_month_rollover()


class DailyCostMeter(PeriodCostMeter):
    """The global daily LLM-spend ceiling (Phase 10, D62) — the anti-"burned in an hour" brake.

    The monthly cap (D58) bounds the *bill*; it does not stop one caller from spending the whole
    month's budget in an afternoon and leaving the public demo returning 503 for weeks. A daily
    sub-cap bounds the blast radius of a bad day: spend stops, and the demo heals itself at the
    next UTC midnight instead of at the next month. Same mechanism, same 503 + Retry-After.
    """

    table = "daily_cost"
    column = "day"
    period_label = "Daily"

    @staticmethod
    def _period_key() -> str:
        return _day_key()

    @staticmethod
    def _seconds_to_rollover() -> int:
        return _seconds_to_day_rollover()


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _seconds_to_month_rollover() -> int:
    now = datetime.now(timezone.utc)
    if now.month == 12:
        nxt = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return int((nxt - now).total_seconds())


def _seconds_to_day_rollover() -> int:
    now = datetime.now(timezone.utc)
    nxt = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)
    return int((nxt - now).total_seconds())
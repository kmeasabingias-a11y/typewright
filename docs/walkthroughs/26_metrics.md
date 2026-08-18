# 26 — `src/typewright/metrics.py` (counting what an analysis cost)

## 1. What this file is for

Every analysis makes a handful of paid LLM calls (detect properties → strategies → test file →
maybe a fix). This file's job is to **add up what those calls cost**, in dollars, for one
analysis — so the response can tell you `llm_cost_usd: 0.0123`. That number is the foundation
for the next unit, which will *stop* an analysis that's about to get too expensive.

The tricky part isn't the addition — it's *where* to do the adding without making a mess. The
LLM calls happen deep inside four different steps. We don't want to pass a "running total"
object down through all of them (that clutters every function with bookkeeping that has nothing
to do with detecting properties). So we use a small trick: a **per-request scratchpad** that the
one place that actually talks to the LLM can find on its own.

Analogy: think of a shared tab at a bar. The route "opens a tab" at the start of a request.
Each LLM call, wherever it happens, quietly adds its charge to whatever tab is currently open.
At the end, the route reads the tab's total and closes it. Nobody has to carry the tab around.

## 2. A mental model

1. **A `CostMeter` is the tab.** A tiny object with a running `total_usd` and a count of calls.

2. **A `contextvar` is "the tab that's currently open."** A `contextvars.ContextVar` is a Python
   variable whose value is *per-execution-context* — each web request gets its own, even when
   several run at once on different threads. The route sets it ("a tab is open"); the LLM code
   reads it ("which tab do I charge?"). Crucially, two requests running at the same time each see
   *their own* meter, never each other's.

3. **One chokepoint does the charging.** Every LLM call in the whole app goes through
   `llm.complete`. That's the single place that asks the LLM library for the call's cost and adds
   it to the open tab (`add_cost`). If no tab is open (a background job, a unit test), `add_cost`
   does nothing — silently. So the steps and their tests never have to know cost exists.

## 3. The whole file

```python
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

from .errors import CostBudgetExceededError


class CostMeter:
    """Accumulates the USD cost of the LLM calls in one analysis, optionally capped by a budget."""

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
```

## 4. Step-by-step

**`CostMeter`.** The tab: a `total_usd` and a `calls` counter, with one `add` method. Deliberately
dumb — it just sums.

**`_active_meter` (the contextvar).** This is "the tab that's currently open," defaulting to
`None` (no tab). Setting it returns a *token*; resetting with that token restores whatever was
there before. Because it's a contextvar, concurrent requests don't clobber each other's tab.

**`_response_cost`.** Turns one raw LLM response into a dollar figure, defensively. LiteLLM usually
attaches the cost it computed at `response._hidden_params['response_cost']`; if it's missing we ask
LiteLLM to compute it from the response. If anything goes wrong — an unpriced model, a malformed
response — we return `0.0` instead of raising. The principle (same as the fix step, D44): a missing
price must never sink a real analysis. We *report* cost; we don't *enforce* it here.

**`add_cost`.** The bridge from the LLM chokepoint to the tab. It looks up the open tab; if there is
one, it charges it. If there isn't (no `cost_scope` active), it does nothing. This is why
`llm.complete` can call `add_cost` unconditionally and the GitHub worker or a unit test — which
never open a scope — are unaffected.

**`cost_scope`.** The context manager the route wraps around the pipeline:
```python
with cost_scope() as meter:
    ...run the four steps...
# meter.total_usd now holds the analysis's total spend
```
It creates a fresh meter, makes it the open tab, hands it back via `yield`, and — no matter what
happens, success or exception — resets the contextvar in `finally` so the tab is always closed.

**How `llm.complete` uses it (see `10_llm.md`).** `complete` now asks Instructor for the call *with*
its raw response (`create_with_completion`) and calls `add_cost(raw)`. Real Instructor clients have
that method; the hand-written fakes in the step tests only have `create()`, so `complete` falls back
to `create()` for them — which is why none of the existing step tests changed.

## 5. What could go wrong (and why the code is shaped to avoid it)

- **Two requests billing each other.** If "the open tab" were a plain global variable, two analyses
  running at once would add to the same total. A **contextvar** gives each request its own value, so
  concurrent analyses keep separate tabs. This is the whole reason for the contextvar instead of a
  module-level variable.
- **A leaked tab.** If we set the contextvar and forgot to reset it, the *next* request reusing that
  thread would keep charging the old meter. `cost_scope` resets in a `finally`, so the tab closes
  even when the pipeline raises (a 500 or a 504).
- **An unpriced model crashing the analysis.** New or renamed models sometimes aren't in LiteLLM's
  price map yet. `_response_cost` swallows that and returns `0.0`, so the worst case is "cost shows
  as 0", never "the analysis 500s because we couldn't price it."
- **Forcing every step to carry a cost argument.** Threading a meter through `infer_properties`,
  `generate_strategies`, `generate_test_file`, and `suggest_fix` would put bookkeeping into four
  functions that have nothing to do with money — and would change all their tests. Charging at the
  single `llm.complete` chokepoint keeps the cost concern in exactly one place.
- **Silently reading $0 forever.** The risk of the contextvar approach is "what if the charge never
  lands?" We sidestep the fragile path (relying on a global LiteLLM callback firing in the right
  thread) by reading the cost *synchronously* from the response inside `complete`, in the same
  thread — so if a scope is open and a real call is made, the charge lands.

## 6. Phase 10 — the global monthly cap (`MonthlyCostMeter`, D58)

The `CostMeter` above is a *per-analysis* tab: it lives in memory for one request and vanishes
when that request ends. That protects against a single runaway analysis (the D52 402), but it does
**nothing** about the bigger worry on a public, unauthenticated demo: *thousands of perfectly
ordinary analyses* slowly running up a bill all month. Ten cents each is fine; ten cents each,
forty thousand times, is not.

So Phase 10 adds a second, different kind of meter — the `MonthlyCostMeter`. Same idea (count the
dollars), but three things change:

1. **It's durable, not in-memory.** The running total lives in **SQLite** — the same `runs.db`
   file the run store uses — in a tiny `monthly_cost` table, one row per month (`"2026-06"`). A
   counter that forgot everything on restart wouldn't be a *cap*: a crash would silently reset the
   month's spend to zero. SQLite means it survives restarts and redeploys.
2. **It's shared across everything.** Because it's a file, the web process **and** the GitHub-App
   worker both read and write the same counter (as long as they point at the same `runs_db_path`).
   One number for the whole service, not one per process.
3. **It blocks *before* spending, not after.** The per-analysis meter charges *after* each call.
   The monthly meter does the opposite: `check()` runs **before** each LLM call and refuses if the
   month is already used up. That ordering matters — once the cap is hit, a new request is turned
   away having spent **zero** on the LLM, instead of paying for one more call every single time.

The code (it lives in the same file, just below `cost_scope`):

```python
class MonthlyCostMeter:
    def __init__(self, db_path: str, limit_usd: float) -> None:
        self._path = db_path
        self.limit_usd = limit_usd

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS monthly_cost ("
            "month TEXT PRIMARY KEY, total_usd REAL NOT NULL)"
        )
        return conn

    def current_total(self) -> float:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT total_usd FROM monthly_cost WHERE month = ?", (_month_key(),)
            ).fetchone()
        finally:
            conn.close()
        return float(row[0]) if row is not None else 0.0

    def check(self) -> None:
        total = self.current_total()
        if total >= self.limit_usd:
            raise MonthlyBudgetExceededError(total, self.limit_usd, _seconds_to_month_rollover())

    def add(self, cost_usd: float) -> None:
        if cost_usd <= 0:
            return
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO monthly_cost (month, total_usd) VALUES (?, ?) "
                    "ON CONFLICT(month) DO UPDATE SET total_usd = total_usd + excluded.total_usd",
                    (_month_key(), cost_usd),
                )
        finally:
            conn.close()

    def add_from_raw(self, raw_completion: object) -> None:
        self.add(_response_cost(raw_completion))
```

**`check()` / `add()` — the gate and the tally.** `check()` reads this month's total and raises
`MonthlyBudgetExceededError` (→ **503**) once it's reached the ceiling. `add()` records a call's
cost with a single atomic SQL `UPSERT` (insert the month's row, or add to it if it's already there)
and *never raises* — the call it's recording already happened, so blocking is the *next* `check()`'s
job. Net effect, same as D52: spend is bounded at the ceiling plus the few calls in flight when it
was crossed.

**Why 503 and not 402?** The per-analysis cap is the *caller's* budget — "your request would cost
too much" (402). The monthly cap is the *operator's* budget being used up — not the caller's fault,
and they can't pay their way past it — so the honest answer is "the service is temporarily
unavailable, come back later" (503 + a `Retry-After` pointing at the month rollover). Reads (`GET /`,
the `?run=` share links, `/health`) make no LLM call, so they keep working while the cap is tripped:
the demo degrades to read-only, not dark.

**Where it's wired (see `10_llm.md`).** There's no contextvar and no global here — `llm.complete`
builds the meter straight from `settings` (`_monthly_meter(settings)`) on the real-LLM-call path
only, calls `check()` before the call and `add_from_raw(raw)` after. Because it's derived from
`settings.runs_db_path`, the worker participates automatically just by sharing that file, and the
unit tests (which never make a real call) never touch SQLite. Set `max_monthly_cost_usd` to 0 to
turn the whole thing off.

`_month_key()` is just today's `"YYYY-MM"` in UTC; `_seconds_to_month_rollover()` is how long until
the 1st of next month, for the `Retry-After` header.

## 7. Change history

- **2026-06-28** — **Created (Phase 9, Unit 1, D51).** Per-request LLM cost accounting: `CostMeter`,
  a `cost_scope()` contextvar, and `add_cost(raw)` called from `llm.complete` (which switched to
  Instructor's `create_with_completion` to see the raw response). Best-effort costing — a price-map
  miss or any error degrades to `0.0` (cost reported, not enforced; the budget is Unit 2). Feeds
  `AnalyzeResponse.metadata.llm_cost_usd`. See `10_llm.md` (the chokepoint change), `06_main.md`
  (the route opens the scope + builds `metadata`), and `03_models.md` (`AnalysisMetadata`).
- **2026-06-28** — **Phase 9 (Unit 2, D52): the meter now enforces a budget.** `CostMeter` gained a
  `limit_usd`, and `add()` raises `CostBudgetExceededError(spent, limit)` the instant the running total
  crosses it; `cost_scope(limit_usd=…)` passes the budget in. Because `add_cost` runs *after* each LLM call,
  the crossing call completes and the next never fires — spend is bounded at "ceiling + one in-flight call."
  The `/v1/analyze` route opens `cost_scope` with `min(request.max_cost_usd, settings.max_cost_usd)` and the
  error surfaces as **402** (the best-effort fix step catches it and drops the fix instead). See `04_errors.md`.
- **2026-06-30** — Phase 10 (D58): added `MonthlyCostMeter` — a durable, SQLite-backed **global monthly** spend
  cap (a `monthly_cost` table in `runs_db_path`, keyed `YYYY-MM` UTC), distinct from the per-request `CostMeter`.
  `check()` (a pre-check *before* each LLM call) raises `MonthlyBudgetExceededError` → **503** + `Retry-After`
  once the month's total reaches the ceiling; `add()`/`add_from_raw()` tally after each call and never raise.
  Wired in `llm.complete` straight from `settings` (no contextvar/global), so the web process and the GitHub-App
  worker share one counter and the tests stay inert. Config `max_monthly_cost_usd` (default $10; ≤ 0 disables).
  See §6 above, `10_llm.md`, and `04_errors.md`.
- **2026-08-16** — Phase 10 (D62): the monthly meter was generalised into **`PeriodCostMeter`** — the table
  name, key column, period key, and rollover are now subclass hooks — with two concrete meters:
  `MonthlyCostMeter` (unchanged behaviour: `monthly_cost`, `YYYY-MM`) and a new **`DailyCostMeter`**
  (`daily_cost`, `YYYY-MM-DD`). Both are pre-checked and billed at the same `llm.complete` chokepoint, and both
  raise the same `MonthlyBudgetExceededError` → **503**, which now carries a `period` label ("Monthly"/"Daily")
  so the body and the `Retry-After` say which ceiling was hit and how long the wait is. *Why a second cap:* the
  monthly one bounds the **bill**; it does nothing about one caller burning the whole month in an afternoon and
  leaving a public demo dead for weeks. The daily cap bounds that blast radius and self-heals at UTC midnight.
  Config `max_daily_cost_usd` (default $2.00; ≤ 0 disables). Verified live: a blocked request returned 503 in
  42 ms with both counters unchanged — **$0 spent**. See `10_llm.md`, `04_errors.md`, `01_config.md`.

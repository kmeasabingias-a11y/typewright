# 27 — `src/typewright/ratelimit.py` (capping how often a client can call)

## 1. What this file is for

The analysis endpoint is expensive — every call spends real LLM money and runs a sandbox. If
the demo is public, one person (or a script) hammering it could run up the bill or starve
everyone else. This file's job is to say **"you can do at most N of these per minute"** and
turn anyone who goes over into a polite **429 "slow down, try again in X seconds."**

It guards two doors: `POST /v1/analyze` (limited **per visitor IP**) and the GitHub webhook
(limited **per installation**, so one repo's PR storm can't flood the queue).

Analogy: a club with a doorman who keeps a tally for each guest. Up to N entries an hour,
fine; the (N+1)th gets "come back later." Each guest has their own tally; one guest hitting
their limit doesn't affect anyone else.

## 2. A mental model

1. **Fixed window.** Time is chopped into fixed 60-second blocks. Each key (an IP, an install)
   gets a counter that resets at the start of every block. Up to `limit` requests in a block are
   allowed; the rest are turned away until the block rolls over. Simple to reason about: "10 per
   minute" means exactly that, per clock-minute.

2. **One interface, two backends.** `RateLimiter` is the contract (`check`). `InMemoryRateLimiter`
   keeps the tallies in a dict inside this one process — zero setup, perfect for a single server.
   `RedisRateLimiter` keeps them in Redis, so several server copies share one tally (and it survives
   restarts). The app picks one from config; the rest of the code only knows the contract.

3. **A limiter outage must not break the app.** If the Redis backend can't reach Redis, it **allows**
   the request (and logs a warning) rather than failing it. Availability beats strictness here —
   and the per-analysis **cost budget** (Unit 2) is still the hard cap on spend.

## 3. The whole file

```python
"""Per-key request rate limiting (Phase 9, Unit 3, D53).

A fixed-window counter: at most ``limit`` requests per ``window_seconds`` for a given key (a client
IP for /v1/analyze, an installation id for the webhook). The ``RateLimiter`` protocol has two
implementations behind it: ``InMemoryRateLimiter`` (per-process, zero-infra, the default — correct
for a single instance) and ``RedisRateLimiter`` (shared across replicas/restarts, the production
path). The route picks one from config; the seam means switching backends needs no route change.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("typewright")


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int  # seconds until the window resets (0 when allowed)


class RateLimiter(Protocol):
    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult: ...


def _retry_after(now: float, window_seconds: int) -> int:
    """Seconds until the current fixed window rolls over."""
    return window_seconds - int(now) % window_seconds


class InMemoryRateLimiter:
    """A per-process fixed-window limiter. One counter per key; resets when the window rolls."""

    def __init__(self) -> None:
        self._state: dict[str, list[int]] = {}  # key -> [window_id, count]

    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = time.time()
        window_id = int(now // window_seconds)
        entry = self._state.get(key)
        if entry is None or entry[0] != window_id:
            entry = [window_id, 0]
            self._state[key] = entry
        entry[1] += 1
        if entry[1] <= limit:
            return RateLimitResult(allowed=True, retry_after=0)
        return RateLimitResult(allowed=False, retry_after=_retry_after(now, window_seconds))


class RedisRateLimiter:
    """A shared fixed-window limiter backed by Redis (INCR + EXPIRE). Fails OPEN on a Redis error.

    Takes a sync redis client (``redis.Redis``). The window key is ``ratelimit:{key}:{window_id}``;
    the first request in a window sets the TTL, so the key self-expires. A Redis outage degrades to
    "allow" (availability over strictness — the U2 cost budget is the hard backstop) with a warning.
    """

    def __init__(self, client) -> None:
        self._client = client

    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = time.time()
        window_id = int(now // window_seconds)
        redis_key = f"ratelimit:{key}:{window_id}"
        try:
            count = self._client.incr(redis_key)
            if count == 1:
                self._client.expire(redis_key, window_seconds)
        except Exception as exc:  # noqa: BLE001 — a limiter outage must not take down the API
            logger.warning("rate limiter (redis) unavailable, allowing request: %s", exc)
            return RateLimitResult(allowed=True, retry_after=0)
        if count <= limit:
            return RateLimitResult(allowed=True, retry_after=0)
        return RateLimitResult(allowed=False, retry_after=_retry_after(now, window_seconds))
```

## 4. Step-by-step

**`RateLimitResult`.** The answer to one check: `allowed` (let it through?) and `retry_after`
(how many seconds until the window resets — goes straight into the 429's `Retry-After` header).

**`RateLimiter` (the contract).** A `Protocol` with one method, `check(key, limit, window_seconds)`.
Anything with that method counts — so a test can pass a tiny "always blocked" stand-in.

**`InMemoryRateLimiter`.** Keeps `key -> [window_id, count]`. On each check it computes the current
window (`now // 60`); if the stored window is stale (or absent), it resets the count to 0. Then it
bumps the count and allows if it's `<= limit`. Only one entry per key, so memory stays bounded.

**`RedisRateLimiter`.** Same idea, but the counter is a Redis key `ratelimit:{key}:{window}`.
`INCR` returns the new count atomically; on the **first** request in a window it sets an `EXPIRE`,
so the key cleans itself up. If Redis raises (down, timeout), it **allows** the request and logs —
the API keeps serving even if the limiter is sick.

**How the route uses it (see `06_main.md`).** `create_app` builds one limiter from
`rate_limit_backend` and stores it on `app.state`; a `get_rate_limiter` dependency hands it to the
routes. `/v1/analyze` checks `analyze:{client_ip}`; the webhook checks `webhook:{installation_id}`.
A blocked check raises `RateLimitedError(retry_after)` → the 429 handler.

## 5. What could go wrong (and why the code is shaped to avoid it)

- **A limiter outage taking down the whole API.** If `RedisRateLimiter` let a Redis error bubble up,
  every analysis would 500 whenever Redis hiccuped. It **fails open** instead — the request goes
  through, a warning is logged, and the cost budget still caps spend.
- **Everyone sharing one tally behind a proxy.** Behind a tunnel/load-balancer, the network peer is
  the *proxy's* IP, so naively all visitors would share one counter. The route resolves the real IP
  from `X-Forwarded-For` — but only when `trust_forwarded_for` is set, because otherwise a client
  could *send* a fake `X-Forwarded-For` to get a fresh tally and dodge the limit. Off by default;
  on only behind a proxy you control.
- **Counts leaking between tests.** The limiter lives on `app.state`, so each `create_app()` gets its
  own — one test's requests never spill into another's. (A process-global limiter would slowly
  accumulate "testclient" hits across the suite and eventually throttle an unrelated test.)
- **Unbounded memory.** The in-memory backend keeps a single `[window_id, count]` per key and
  overwrites it when the window rolls, so it doesn't grow per-request. (Redis keys self-expire via
  `EXPIRE`.) A truly huge number of distinct IPs would still grow the dict — fine for a demo; Redis
  is the answer at real scale.
- **Burst at the window edge.** Fixed windows allow up to `2×limit` across a window boundary (end of
  one minute + start of the next). Acceptable for a demo; a sliding window would smooth it, at the
  cost of more state. Noted, not built.

## 6. Change history

- **2026-06-28** — **Created (Phase 9, Unit 3, D53).** Fixed-window rate limiting behind a `RateLimiter`
  seam: `InMemoryRateLimiter` (default, per-process) + `RedisRateLimiter` (`INCR`+`EXPIRE`, shared,
  fail-open), chosen by `rate_limit_backend`. Limits `POST /v1/analyze` per IP and the webhook per
  installation → **429** + `Retry-After` (`RateLimitedError`). The client IP honors `X-Forwarded-For`
  only when `trust_forwarded_for` is set. See `06_main.md` (wiring + `_client_ip`), `04_errors.md`
  (`RateLimitedError`), `01_config.md` (the settings).

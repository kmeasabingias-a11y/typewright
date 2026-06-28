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
"""Tests for the fixed-window rate limiter (Phase 9, Unit 3, D53)."""

from typewright.ratelimit import InMemoryRateLimiter, RedisRateLimiter


def test_in_memory_allows_up_to_limit_then_blocks():
    limiter = InMemoryRateLimiter()
    for _ in range(3):
        assert limiter.check("k", limit=3, window_seconds=60).allowed
    blocked = limiter.check("k", limit=3, window_seconds=60)
    assert not blocked.allowed
    assert 0 < blocked.retry_after <= 60


def test_in_memory_separate_keys_are_independent():
    limiter = InMemoryRateLimiter()
    assert limiter.check("a", 1, 60).allowed
    assert not limiter.check("a", 1, 60).allowed
    assert limiter.check("b", 1, 60).allowed  # different key, own window


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key, seconds):
        pass


def test_redis_limiter_counts_with_fake_client():
    limiter = RedisRateLimiter(_FakeRedis())
    assert limiter.check("k", 2, 60).allowed
    assert limiter.check("k", 2, 60).allowed
    assert not limiter.check("k", 2, 60).allowed


class _BrokenRedis:
    def incr(self, key):
        raise RuntimeError("redis down")


def test_redis_limiter_fails_open():
    limiter = RedisRateLimiter(_BrokenRedis())
    assert limiter.check("k", 1, 60).allowed  # fail-open on error
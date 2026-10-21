"""RedisRateLimiter contra Redis real (fase 10.2): atomicidad entre réplicas y paridad con GCRA.

docker compose --profile scale up -d ; uv run pytest -m integration -k redis_rate_limit
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator

import pytest

from headache_core.adapters.redis_rate_limit import RedisRateLimiter
from headache_core.config import ScaleRedisSettings, Settings
from headache_core.ports import RateLimiterUnavailableError, RateLimitRequest, RateLimitRule

pytestmark = pytest.mark.integration

RULE = RateLimitRule("user_public", 10, 60.0)


@pytest.fixture
async def limiter() -> AsyncIterator[RedisRateLimiter]:
    # Prefijo único por test: no se pisan entre ejecuciones ni hay que limpiar (todo caduca).
    instance = RedisRateLimiter.from_settings(
        Settings().scale_redis, key_prefix=f"{{test-{uuid.uuid4().hex[:8]}}}"
    )
    try:
        yield instance
    finally:
        await instance.aclose()


async def test_concurrent_replicas_never_grant_more_than_the_limit(
    limiter: RedisRateLimiter,
) -> None:
    other_replica = RedisRateLimiter.from_settings(
        Settings().scale_redis, key_prefix=limiter._prefix
    )
    request = [RateLimitRequest("user:ana", RULE)]
    try:
        decisions = await asyncio.gather(
            *((limiter if i % 2 else other_replica).acquire(request) for i in range(50))
        )
    finally:
        await other_replica.aclose()

    assert sum(d.allowed for d in decisions) == 10
    denied = [d for d in decisions if not d.allowed]
    assert all(d.limited_by == "user_public" and 0 < d.retry_after <= 6.1 for d in denied)


async def test_burst_and_remaining_match_the_in_memory_gcra(limiter: RedisRateLimiter) -> None:
    rule = RateLimitRule("user_anonymous", 3, 60.0)
    request = [RateLimitRequest("ip:10.0.0.1", rule)]

    decisions = [await limiter.acquire(request) for _ in range(4)]

    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert [d.remaining for d in decisions[:3]] == [2, 1, 0]
    assert decisions[3].retry_after == pytest.approx(20.0, abs=0.5)


async def test_multi_key_is_atomic_and_keys_expire(limiter: RedisRateLimiter) -> None:
    rpm = RateLimitRule("model_rpm", 10, 60.0)
    tpm = RateLimitRule("model_tpm", 1000, 60.0)

    first = await limiter.acquire(
        [RateLimitRequest("model:x:rpm", rpm), RateLimitRequest("model:x:tpm", tpm, 900)]
    )
    second = await limiter.acquire(
        [RateLimitRequest("model:x:rpm", rpm), RateLimitRequest("model:x:tpm", tpm, 900)]
    )
    only_rpm = await limiter.acquire([RateLimitRequest("model:x:rpm", rpm)])

    assert first.allowed and not second.allowed
    assert second.limited_by == "model_tpm"
    assert only_rpm.remaining == 8  # La denegada no consumió RPM.
    ttl = await limiter._client.pttl(f"{limiter._prefix}:model:x:tpm")
    assert 0 < ttl <= 54_100  # 900 tokens a 1000/min tardan 54 s en recuperarse.


async def test_unreachable_redis_fails_fast_and_cools_down() -> None:
    limiter = RedisRateLimiter.from_settings(
        ScaleRedisSettings(port=1), key_prefix="{test}", cooldown_seconds=30.0
    )
    request = [RateLimitRequest("user:ana", RULE)]
    try:
        started = time.perf_counter()
        with pytest.raises(RateLimiterUnavailableError):
            await limiter.acquire(request)
        first = time.perf_counter() - started
        started = time.perf_counter()
        with pytest.raises(RateLimiterUnavailableError, match="enfriamiento"):
            await limiter.acquire(request)
        second = time.perf_counter() - started
    finally:
        await limiter.aclose()

    # Sin los reintentos por defecto de redis-py (~7 s) y sin volver a intentarlo en 30 s.
    assert first < 3.0
    assert second < 0.01

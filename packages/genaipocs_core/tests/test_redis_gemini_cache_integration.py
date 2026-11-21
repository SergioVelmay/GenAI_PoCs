"""Registro compartido de cachés explícitas de Gemini contra Redis 8 y Valkey reales (10.G3).

docker compose --profile scale up -d ; uv run pytest -m integration -k redis_gemini
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator

import pytest

from genaipocs_core.adapters.gemini_cache import SharedCache
from genaipocs_core.adapters.redis_gemini_cache import RedisCacheRegistry
from genaipocs_core.config import ScaleRedisSettings

pytestmark = pytest.mark.integration


@pytest.fixture
async def registry(scale_redis: ScaleRedisSettings) -> AsyncIterator[RedisCacheRegistry]:
    # Prefijo único por test: todo caduca solo, no hay que limpiar.
    instance = RedisCacheRegistry.from_settings(
        scale_redis, key_prefix=f"test-gc-{uuid.uuid4().hex[:8]}"
    )
    try:
        yield instance
    finally:
        await instance.aclose()


async def test_first_writer_wins_between_replicas(
    registry: RedisCacheRegistry, scale_redis: ScaleRedisSettings
) -> None:
    other = RedisCacheRegistry.from_settings(scale_redis, key_prefix=registry._prefix)
    expires = time.time() + 60
    try:
        results = await asyncio.gather(
            *(
                (registry if i % 2 else other).put_if_absent(
                    "prefix", SharedCache(f"cachedContents/{i}", expires)
                )
                for i in range(10)
            )
        )
    finally:
        await other.aclose()

    # Las diez réplicas acaban citando la misma caché.
    assert len({r.name for r in results}) == 1
    assert await registry.get("prefix") == results[0]


async def test_the_entry_expires_with_the_cache(registry: RedisCacheRegistry) -> None:
    await registry.put_if_absent("prefix", SharedCache("cachedContents/1", time.time() + 1))

    assert await registry.get("prefix") is not None
    await asyncio.sleep(1.5)
    assert await registry.get("prefix") is None


async def test_an_already_expired_cache_is_not_registered(registry: RedisCacheRegistry) -> None:
    stale = SharedCache("cachedContents/old", time.time() - 1)

    assert await registry.put_if_absent("prefix", stale) == stale
    assert await registry.get("prefix") is None

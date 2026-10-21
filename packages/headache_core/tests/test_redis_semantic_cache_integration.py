"""RedisSemanticCache contra Redis 8 real (fase 10.3): KNN por espacio, caducidad y dimensiones.

docker compose --profile scale up -d ; uv run pytest -m integration -k redis_semantic
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator

import pytest
from redis.exceptions import ResponseError

from headache_core.adapters.memory import HashingEmbedding
from headache_core.adapters.redis_semantic_cache import RedisSemanticCache
from headache_core.application.semantic_cache import SemanticCache
from headache_core.config import Settings
from headache_core.domain.semantic_cache import CacheScope
from headache_core.ports.semantic_cache import CacheEntry

pytestmark = pytest.mark.integration

PUBLIC_ES = CacheScope(("public",), "es", "v1", "extractive")


@pytest.fixture
async def store() -> AsyncIterator[RedisSemanticCache]:
    instance = RedisSemanticCache.from_settings(
        Settings().scale_redis, dimensions=256, key_prefix=f"{{test-sc-{uuid.uuid4().hex[:8]}}}"
    )
    try:
        yield instance
    finally:
        with contextlib.suppress(ResponseError):  # El test pudo no llegar a crear el índice.
            await instance._client.ft(instance._index).dropindex(delete_documents=True)
        await instance.aclose()


async def test_same_decisions_as_in_memory(store: RedisSemanticCache) -> None:
    cache = SemanticCache(store, HashingEmbedding(256), threshold=0.55)
    first = await cache.lookup("¿A qué hora abren las puertas en Barcelona?", PUBLIC_ES)
    assert await cache.store_answer(first, json.dumps({"answer": "15 minutos antes"}))

    paraphrase = await cache.lookup("¿Cuándo abren las puertas en Barcelona?", PUBLIC_ES)
    other_city = await cache.lookup("¿A qué hora abren las puertas en Chicago?", PUBLIC_ES)
    other_scope = await cache.lookup(
        "¿A qué hora abren las puertas en Barcelona?",
        CacheScope(("public",), "es", "v2", "extractive"),
    )

    assert paraphrase.reason == "hit"
    assert json.loads(paraphrase.payload or "{}") == {"answer": "15 minutos antes"}
    assert other_city.reason == "facet_mismatch"
    assert other_scope.reason == "miss"


async def test_entries_expire_with_their_ttl(store: RedisSemanticCache) -> None:
    embedding = tuple(await HashingEmbedding(256).embed_query("puertas Barcelona"))
    entry = CacheEntry("e1", PUBLIC_ES.key(), "puertas Barcelona", "{}", embedding, "{}")
    await store.put(entry, ttl_seconds=1)

    assert len(await store.nearest(PUBLIC_ES.key(), embedding, limit=3)) == 1
    await asyncio.sleep(1.5)
    assert await store.nearest(PUBLIC_ES.key(), embedding, limit=3) == []


async def test_wrong_dimensions_are_rejected(store: RedisSemanticCache) -> None:
    with pytest.raises(ValueError, match="dimensiones"):
        await store.nearest(PUBLIC_ES.key(), [0.1] * 10, limit=3)

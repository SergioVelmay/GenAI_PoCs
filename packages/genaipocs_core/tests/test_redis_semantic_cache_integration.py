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

from genaipocs_core.adapters.memory import HashingEmbedding
from genaipocs_core.adapters.redis_semantic_cache import RedisSemanticCache
from genaipocs_core.application.semantic_cache import SemanticCache
from genaipocs_core.config import ScaleRedisSettings
from genaipocs_core.domain.semantic_cache import CacheScope
from genaipocs_core.ports.semantic_cache import CacheEntry

pytestmark = pytest.mark.integration

PUBLIC_ES = CacheScope(("public",), "es", "v1", "extractive")


@pytest.fixture
async def store(scale_redis: ScaleRedisSettings) -> AsyncIterator[RedisSemanticCache]:
    instance = RedisSemanticCache.from_settings(
        scale_redis, dimensions=256, key_prefix=f"{{test-sc-{uuid.uuid4().hex[:8]}}}"
    )
    try:
        yield instance
    finally:
        # Valkey no admite `DD` en FT.DROPINDEX: se borra solo el índice (las claves caducan).
        with contextlib.suppress(ResponseError):  # El test pudo no llegar a crear el índice.
            await instance._client.ft(instance._index).dropindex()
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


async def test_nearest_first_without_sortby(store: RedisSemanticCache) -> None:
    """Valkey no tiene SORTBY ni `AS` en el KNN: la distancia sale de `__embedding_score` y el
    orden lo garantiza el adaptador (10.G2)."""
    embeddings = HashingEmbedding(256)
    texts = ["puertas Barcelona", "puertas Barcelona hora", "aparcamiento Madrid coche"]
    for i, text in enumerate(texts):
        vector = tuple(await embeddings.embed_query(text))
        entry = CacheEntry(f"e{i}", PUBLIC_ES.key(), text, "{}", vector, f'{{"i": {i}}}')
        await store.put(entry, ttl_seconds=60)

    query = await embeddings.embed_query("puertas Barcelona")
    candidates = await store.nearest(PUBLIC_ES.key(), query, limit=3)

    similarities = [c.similarity for c in candidates]
    assert candidates[0].entry.question == "puertas Barcelona"
    assert similarities == sorted(similarities, reverse=True)
    assert similarities[0] > 0.99
    assert candidates[0].entry.payload == '{"i": 0}'


async def test_wrong_dimensions_are_rejected(store: RedisSemanticCache) -> None:
    with pytest.raises(ValueError, match="dimensiones"):
        await store.nearest(PUBLIC_ES.key(), [0.1] * 10, limit=3)

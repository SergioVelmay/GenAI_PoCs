"""Caché semántica en Redis 8 (fase 10.3): Query Engine con índice vectorial HNSW.

- Cada entrada es un hash `{headache-sc}:<id>` con el espacio (TAG), el vector (FLOAT32) y la
  respuesta; caduca sola (`EXPIRE`) y el índice la olvida al caducar.
- La búsqueda es KNN filtrado por espacio: `(@scope:{<espacio>})=>[KNN k @embedding $vec]`.
  El espacio es un hash hexadecimal (`CacheScope.key()`): sin caracteres que escapar en un TAG.
- Un índice por dimensión de embedding: cambiar de modelo de embeddings no mezcla vectores.

Es lo que hacen por debajo la política `azure-openai-semantic-cache-lookup` de APIM (con Azure
Managed Redis) y ElastiCache (Valkey) con búsqueda vectorial en AWS.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from typing import Self

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.commands.search.field import TagField, TextField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query
from redis.exceptions import RedisError, ResponseError

from headache_core.config import ScaleRedisSettings
from headache_core.ports.semantic_cache import (
    CacheCandidate,
    CacheEntry,
    SemanticCacheUnavailableError,
)


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


class RedisSemanticCache:
    def __init__(
        self, client: Redis, *, dimensions: int, key_prefix: str = "{headache-sc}"
    ) -> None:
        self._client = client
        self._dimensions = dimensions
        self._prefix = key_prefix
        # El nombre lleva el prefijo: dos cachés (o tests) con prefijos distintos no comparten
        # índice (un índice solo indexa las claves del prefijo con el que se creó).
        self._index = f"idx:{key_prefix.strip('{}')}:{dimensions}"
        self._ready = False

    @classmethod
    def from_settings(
        cls, settings: ScaleRedisSettings, *, dimensions: int, key_prefix: str = "{headache-sc}"
    ) -> Self:
        client = Redis(
            host=settings.host,
            port=settings.port,
            db=settings.db,
            password=settings.password.get_secret_value() or None,
            socket_timeout=settings.timeout_seconds,
            socket_connect_timeout=settings.timeout_seconds,
            retry=Retry(NoBackoff(), 0),
            max_connections=settings.max_connections,
        )
        return cls(client, dimensions=dimensions, key_prefix=key_prefix)

    async def ensure_index(self) -> None:
        if self._ready:
            return
        try:
            await self._client.ft(self._index).info()  # type: ignore[no-untyped-call]
        except ResponseError:
            await self._client.ft(self._index).create_index(
                [
                    TagField("scope"),
                    TextField("question", no_stem=True),
                    VectorField(
                        "embedding",
                        "HNSW",
                        {
                            "TYPE": "FLOAT32",
                            "DIM": self._dimensions,
                            "DISTANCE_METRIC": "COSINE",
                        },
                    ),
                ],
                definition=IndexDefinition(  # type: ignore[no-untyped-call]
                    prefix=[f"{self._prefix}:{self._dimensions}:"], index_type=IndexType.HASH
                ),
            )
        self._ready = True

    async def nearest(
        self, scope: str, embedding: Sequence[float], *, limit: int
    ) -> list[CacheCandidate]:
        self._check(embedding)
        query = (
            Query(f"(@scope:{{{scope}}})=>[KNN {limit} @embedding $vec AS distance]")
            .sort_by("distance")
            .return_fields("distance", "scope", "question", "facets", "answer")
            .paging(0, limit)
            .dialect(2)
        )
        try:
            await self.ensure_index()
            result = await self._client.ft(self._index).search(
                query, query_params={"vec": _pack(embedding)}
            )
        except RedisError as exc:
            raise SemanticCacheUnavailableError(str(exc)) from exc
        candidates = []
        for doc in result.docs:
            entry = CacheEntry(
                id=doc.id.rsplit(":", 1)[-1],
                scope=_text(doc.scope),
                question=_text(doc.question),
                facets=_text(doc.facets),
                # El vector no viaja de vuelta: no hace falta para decidir y pesa 4 KB.
                embedding=(),
                payload=_text(doc.answer),
            )
            # Redis devuelve distancia coseno (1 - similitud).
            candidates.append(CacheCandidate(entry, 1.0 - float(doc.distance)))
        return candidates

    async def put(self, entry: CacheEntry, *, ttl_seconds: int) -> None:
        self._check(entry.embedding)
        key = f"{self._prefix}:{self._dimensions}:{entry.id}"
        try:
            await self.ensure_index()
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.hset(
                    key,
                    mapping={
                        "scope": entry.scope,
                        "question": entry.question,
                        "facets": entry.facets,
                        # "payload" choca con un argumento de `Document` de redis-py al leer.
                        "answer": entry.payload,
                        "embedding": _pack(entry.embedding),
                    },
                )
                pipe.expire(key, ttl_seconds)
                await pipe.execute()
        except RedisError as exc:
            raise SemanticCacheUnavailableError(str(exc)) from exc

    def _check(self, embedding: Sequence[float]) -> None:
        if len(embedding) != self._dimensions:
            raise ValueError(
                f"El embedding tiene {len(embedding)} dimensiones; el índice, {self._dimensions}."
            )

    async def aclose(self) -> None:
        await self._client.aclose()


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)

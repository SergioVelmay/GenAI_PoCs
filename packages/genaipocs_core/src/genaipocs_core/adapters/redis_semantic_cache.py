"""Caché semántica en Redis 8 o Valkey (fases 10.3 y 10.G2): índice vectorial HNSW.

- Cada entrada es un hash `genaipocs-sc:<dimensiones>:<id>` con el espacio (TAG), el vector
  (FLOAT32) y la respuesta; caduca sola (`EXPIRE`) y el índice la olvida al caducar.
- La búsqueda es KNN filtrado por espacio: `(@scope:{<espacio>})=>[KNN k @embedding $vec]`.
  El espacio es un hash hexadecimal (`CacheScope.key()`): sin caracteres que escapar en un TAG.
- Un índice por dimensión de embedding: cambiar de modelo de embeddings no mezcla vectores.
- **Portable a Valkey** (10.G2): la búsqueda vectorial de Memorystore for Valkey solo admite
  campos `VECTOR`, `TAG` y `NUMERIC` (sin `TEXT`), no tiene `SORTBY` y no acepta `AS` dentro del
  KNN. Así que el índice solo declara lo que se filtra (espacio) y lo que se busca (vector); la
  pregunta, las facetas y la respuesta son campos del hash que se devuelven con `RETURN` sin
  indexarse; la distancia se lee de `__embedding_score` (el nombre por defecto en los dos
  motores) y se ordena aquí. Se prueba contra Redis 8 y contra `valkey-bundle`.

Es lo que hacen por debajo la política `azure-openai-semantic-cache-lookup` de APIM (con Azure
Managed Redis), ElastiCache (Valkey) en AWS y Memorystore for Valkey en GCP.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from typing import Self

from redis.asyncio import Redis
from redis.commands.search.field import TagField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query
from redis.exceptions import RedisError, ResponseError

from genaipocs_core.adapters.redis_client import scale_redis_client
from genaipocs_core.config import GcpSettings, ScaleRedisSettings
from genaipocs_core.ports.semantic_cache import (
    CacheCandidate,
    CacheEntry,
    SemanticCacheUnavailableError,
)

# Nombre que dan Redis y Valkey a la distancia del KNN cuando no se usa `AS`.
SCORE_FIELD = "__embedding_score"


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


class RedisSemanticCache:
    def __init__(self, client: Redis, *, dimensions: int, key_prefix: str = "genaipocs-sc") -> None:
        self._client = client
        self._dimensions = dimensions
        # Sin *hash tag*: Valkey rechaza un `PREFIX` de índice con `{...}` (medido en la 10.G2) y
        # aquí no hace falta (cada operación toca una clave; en clúster, la búsqueda recorre
        # todos los shards). Se quitan las llaves de un prefijo antiguo (`{genaipocs-sc}`).
        self._prefix = key_prefix.strip("{}")
        # El nombre lleva el prefijo: dos cachés (o tests) con prefijos distintos no comparten
        # índice (un índice solo indexa las claves del prefijo con el que se creó).
        self._index = f"idx:{self._prefix}:{dimensions}"
        self._ready = False

    @classmethod
    def from_settings(
        cls,
        settings: ScaleRedisSettings,
        *,
        dimensions: int,
        key_prefix: str = "genaipocs-sc",
        gcp: GcpSettings | None = None,
    ) -> Self:
        return cls(scale_redis_client(settings, gcp), dimensions=dimensions, key_prefix=key_prefix)

    async def ensure_index(self) -> None:
        if self._ready:
            return
        try:
            await self._client.ft(self._index).info()  # type: ignore[no-untyped-call]
        except ResponseError:
            await self._client.ft(self._index).create_index(
                [
                    TagField("scope"),
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
            Query(f"(@scope:{{{scope}}})=>[KNN {limit} @embedding $vec]")
            .return_fields(SCORE_FIELD, "scope", "question", "facets", "answer")
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
            # Distancia coseno (1 - similitud).
            distance = float(_text(getattr(doc, SCORE_FIELD)))
            candidates.append(CacheCandidate(entry, 1.0 - distance))
        # Sin `SORTBY` en Valkey: el orden (más parecido primero) se garantiza aquí.
        candidates.sort(key=lambda candidate: candidate.similarity, reverse=True)
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

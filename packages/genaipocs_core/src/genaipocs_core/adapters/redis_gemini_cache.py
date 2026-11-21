"""Registro compartido de cachés explícitas de Gemini en el Redis/Valkey de escala (10.G3).

Una clave por prefijo (`genaipocs-gc:<hash del prefijo>`) con el nombre del `CachedContent` y su
caducidad, y el mismo TTL que la caché: cuando Gemini la borra por TTL, la entrada desaparece a la
vez y nadie cita una caché muerta. `SET NX PX` decide quién gana si dos réplicas la crean a la vez.

Equivale a compartir el `cachePoint` de Bedrock o el *prompt caching* de Azure OpenAI, que no lo
necesitan: allí la caché es implícita y del proveedor. En Gemini la explícita es un recurso que
se paga por hora, y sin registro compartido cada réplica paga el suyo.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Self

from redis.asyncio import Redis
from redis.exceptions import RedisError

from genaipocs_core.adapters.gemini_cache import CacheRegistryUnavailableError, SharedCache
from genaipocs_core.adapters.redis_client import scale_redis_client
from genaipocs_core.config import GcpSettings, ScaleRedisSettings


class RedisCacheRegistry:
    def __init__(
        self,
        client: Redis,
        *,
        key_prefix: str = "genaipocs-gc",
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._prefix = key_prefix
        self._wall_clock = wall_clock

    @classmethod
    def from_settings(
        cls,
        settings: ScaleRedisSettings,
        *,
        key_prefix: str = "genaipocs-gc",
        gcp: GcpSettings | None = None,
    ) -> Self:
        return cls(scale_redis_client(settings, gcp), key_prefix=key_prefix)

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    async def get(self, key: str) -> SharedCache | None:
        try:
            raw = await self._client.get(self._key(key))
        except RedisError as exc:
            raise CacheRegistryUnavailableError(str(exc)) from exc
        if raw is None:
            return None
        data = json.loads(raw)
        return SharedCache(str(data["name"]), float(data["expires_at"]))

    async def put_if_absent(self, key: str, cache: SharedCache) -> SharedCache:
        ttl_ms = int((cache.expires_at - self._wall_clock()) * 1000)
        if ttl_ms <= 0:
            return cache
        value = json.dumps({"name": cache.name, "expires_at": cache.expires_at})
        try:
            if await self._client.set(self._key(key), value, nx=True, px=ttl_ms):
                return cache
        except RedisError as exc:
            raise CacheRegistryUnavailableError(str(exc)) from exc
        # Otra réplica llegó antes: manda la suya (si caducó justo ahora, la propia).
        return await self.get(key) or cache

    async def aclose(self) -> None:
        await self._client.aclose()

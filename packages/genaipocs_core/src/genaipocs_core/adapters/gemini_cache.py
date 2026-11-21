"""Caché explícita de Gemini (2.G3): un `CachedContent` por prefijo estable, reutilizado.

Implícita frente a explícita:

- **Implícita** (por defecto): Gemini cachea solo los prefijos repetidos de >= 4096 tokens. No se
  paga la escritura, pero el acierto no está garantizado.
- **Explícita**: se crea un recurso `CachedContent` con el prefijo (instrucción de sistema, tools
  y mensajes hasta el `cache_checkpoint`) y las peticiones lo citan por nombre. La lectura está
  garantizada (90 % de descuento); la creación se paga a precio de entrada y el almacenamiento
  por hora, mientras viva (TTL). Equivale al `cachePoint` de Bedrock, pero como recurso aparte.

**Registro compartido entre réplicas** (10.G3, heredado de la 2.G3): por defecto el registro vive
en el proceso y cada réplica crea (y paga) su propia caché del mismo prefijo. Con un
`CacheRegistry` compartido (`GEMINI__CACHE_REGISTRY=redis`: el Redis o el Valkey de escala) se
apunta qué `CachedContent` sirve a cada prefijo y las demás réplicas lo reutilizan:

- **Gana el primero** (`SET NX` con el TTL de la caché): si dos réplicas la crean a la vez, la
  que pierde borra la suya al momento y usa la registrada. Sin candados distribuidos: la carrera
  cuesta, como mucho, una creación de más.
- **Nadie borra al cerrar**: una réplica que se apaga no sabe si otras siguen usando la caché;
  caduca sola por su TTL (la entrada del registro, a la vez). Ese almacenamiento se paga.
- Si el registro no responde, se sigue como antes: caché por réplica.

La caché nunca hace fallar una petición: si no se puede crear (modelo sin caché explícita,
prefijo por debajo del mínimo, cuota), la petición sigue con la caché implícita.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from google.genai import errors, types

logger = logging.getLogger(__name__)

# Margen antes de la expiración: no citar una caché que puede caducar en mitad de la petición.
_EXPIRY_MARGIN_S = 30.0


class GeminiCaches(Protocol):
    """Lo que el registro usa de `Client.aio.caches` (permite un doble en los tests)."""

    async def create(
        self, *, model: str, config: types.CreateCachedContentConfig
    ) -> types.CachedContent: ...

    async def delete(self, *, name: str) -> object: ...


@dataclass(frozen=True, slots=True)
class CachePrefix:
    """Lo que se congela en la caché: Gemini no admite estos campos junto a `cached_content`."""

    model: str
    system: str | None
    contents: tuple[types.Content, ...]
    tools: tuple[types.Tool, ...] = ()
    tool_config: types.ToolConfig | None = None

    def key(self) -> str:
        payload = {
            "model": self.model,
            "system": self.system,
            "contents": [c.model_dump(mode="json", exclude_none=True) for c in self.contents],
            "tools": [t.model_dump(mode="json", exclude_none=True) for t in self.tools],
            "tool_config": (
                self.tool_config.model_dump(mode="json", exclude_none=True)
                if self.tool_config
                else None
            ),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CacheHit:
    name: str
    # Tokens escritos al crearla en esta petición (0 si ya existía): se cobran a precio de entrada.
    write_tokens: int


@dataclass(slots=True)
class CreatedCache:
    """Para calcular el almacenamiento: tokens x tiempo vivo x precio por hora."""

    name: str
    model: str
    tokens: int
    created_at: float
    expires_at: float
    deleted_at: float | None = None

    def stored_seconds(self, now: float) -> float:
        end = self.deleted_at if self.deleted_at is not None else min(now, self.expires_at)
        return max(end - self.created_at, 0.0)


@dataclass(frozen=True, slots=True)
class SharedCache:
    name: str
    # Reloj de pared (epoch): lo leen procesos distintos, cuyos relojes monotónicos no casan.
    expires_at: float


class CacheRegistryUnavailableError(Exception):
    """El registro compartido no responde: se sigue con la caché de la réplica."""


class CacheRegistry(Protocol):
    """Qué `CachedContent` sirve a cada prefijo, compartido entre réplicas."""

    async def get(self, key: str) -> SharedCache | None: ...

    async def put_if_absent(self, key: str, cache: SharedCache) -> SharedCache:
        """Registra `cache` si nadie lo ha hecho; devuelve la que queda (propia o ganadora)."""
        ...


class ExplicitCache:
    def __init__(
        self,
        caches: GeminiCaches,
        *,
        ttl_seconds: int,
        clock: Callable[[], float] = time.monotonic,
        registry: CacheRegistry | None = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._caches = caches
        self._ttl = ttl_seconds
        self._clock = clock
        self._registry = registry
        self._wall_clock = wall_clock
        self._live: dict[str, CreatedCache] = {}
        # Cachés que creó otra réplica (registro compartido): nombre y caducidad en `clock`.
        self._borrowed: dict[str, tuple[str, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Prefijos o modelos que la API rechazó (400): no se vuelve a intentar.
        self._rejected_keys: set[str] = set()
        self._rejected_models: set[str] = set()
        self.created: list[CreatedCache] = []

    async def lookup_or_create(self, prefix: CachePrefix) -> CacheHit | None:
        if prefix.model in self._rejected_models:
            return None
        key = prefix.key()
        if key in self._rejected_keys:
            return None
        async with self._locks.setdefault(key, asyncio.Lock()):
            live = self._live.get(key)
            if live is not None and live.expires_at - _EXPIRY_MARGIN_S > self._clock():
                return CacheHit(live.name, write_tokens=0)
            borrowed = self._borrowed.get(key)
            if borrowed is not None and borrowed[1] - _EXPIRY_MARGIN_S > self._clock():
                return CacheHit(borrowed[0], write_tokens=0)
            shared = await self._lookup_shared(key)
            if shared is not None:
                return CacheHit(shared, write_tokens=0)
            return await self._create(key, prefix)

    async def _lookup_shared(self, key: str) -> str | None:
        if self._registry is None:
            return None
        try:
            shared = await self._registry.get(key)
        except CacheRegistryUnavailableError as exc:
            logger.warning("Registro de cachés no disponible (%s): caché por réplica.", exc)
            return None
        if shared is None:
            return None
        return self._borrow(key, shared)

    def _borrow(self, key: str, shared: SharedCache) -> str | None:
        remaining = shared.expires_at - self._wall_clock()
        if remaining <= _EXPIRY_MARGIN_S:
            return None
        self._borrowed[key] = (shared.name, self._clock() + remaining)
        return shared.name

    async def _create(self, key: str, prefix: CachePrefix) -> CacheHit | None:
        config = types.CreateCachedContentConfig(
            display_name=f"genaipocs-{key[:16]}",
            system_instruction=prefix.system,
            contents=list(prefix.contents),
            tools=list(prefix.tools) or None,
            tool_config=prefix.tool_config,
            ttl=f"{self._ttl}s",
        )
        try:
            cache = await self._caches.create(model=prefix.model, config=config)
        except errors.APIError as exc:
            if exc.code == 400:
                # Modelo sin caché explícita (Gemini 4) o prefijo por debajo del mínimo.
                self._reject(key, prefix.model, exc)
            else:
                logger.warning("No se pudo crear la caché explícita (%s): se sigue sin ella.", exc)
            return None
        if not cache.name:
            return None
        now = self._clock()
        tokens = cache.usage_metadata.total_token_count if cache.usage_metadata else None
        created = CreatedCache(cache.name, prefix.model, tokens or 0, now, now + self._ttl)
        self.created.append(created)
        winner = await self._register(key, cache.name)
        if winner is not None and winner.name != cache.name:
            # Otra réplica registró la suya mientras tanto: se usa esa y la propia se borra ya
            # (la escritura ya se pagó; el almacenamiento, no).
            await self._delete(created)
            name = self._borrow(key, winner) or cache.name
            return CacheHit(name, write_tokens=created.tokens)
        self._live[key] = created
        return CacheHit(cache.name, write_tokens=created.tokens)

    async def _register(self, key: str, name: str) -> SharedCache | None:
        if self._registry is None:
            return None
        mine = SharedCache(name, self._wall_clock() + self._ttl)
        try:
            return await self._registry.put_if_absent(key, mine)
        except CacheRegistryUnavailableError as exc:
            logger.warning("No se pudo registrar %s (%s): solo la usa esta réplica.", name, exc)
            return None

    async def _delete(self, created: CreatedCache) -> bool:
        try:
            await self._caches.delete(name=created.name)
        except errors.APIError as exc:
            logger.warning("No se pudo borrar %s (caducará sola): %s", created.name, exc)
            return False
        created.deleted_at = self._clock()
        return True

    def _reject(self, key: str, model: str, exc: errors.APIError) -> None:
        message = str(exc).lower()
        if "too small" in message or "min_total_token_count" in message:
            self._rejected_keys.add(key)
        else:
            self._rejected_models.add(model)
        logger.warning("Caché explícita rechazada para %s (%s): se usa la implícita.", model, exc)

    async def aclose(self) -> None:
        """Borra las cachés vivas: el almacenamiento se paga hasta que caducan o se borran.

        Con registro compartido no se borra nada: otras réplicas pueden estar usándolas.
        """
        if self._registry is not None:
            if self._live:
                logger.info(
                    "%d cachés explícitas compartidas se dejan caducar (TTL %d s).",
                    len(self._live),
                    self._ttl,
                )
            close = getattr(self._registry, "aclose", None)
            if close is not None:
                await close()
            return
        for key, live in list(self._live.items()):
            if await self._delete(live):
                del self._live[key]

"""Caché explícita de Gemini (2.G3): un `CachedContent` por prefijo estable, reutilizado.

Implícita frente a explícita:

- **Implícita** (por defecto): Gemini cachea solo los prefijos repetidos de >= 4096 tokens. No se
  paga la escritura, pero el acierto no está garantizado.
- **Explícita**: se crea un recurso `CachedContent` con el prefijo (instrucción de sistema, tools
  y mensajes hasta el `cache_checkpoint`) y las peticiones lo citan por nombre. La lectura está
  garantizada (90 % de descuento); la creación se paga a precio de entrada y el almacenamiento
  por hora, mientras viva (TTL). Equivale al `cachePoint` de Bedrock, pero como recurso aparte.

El registro vive en el proceso: cada réplica crea su propia caché del mismo prefijo. En
producción se compartiría el nombre (p. ej. en Redis) para pagar un solo almacenamiento.

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


class ExplicitCache:
    def __init__(
        self,
        caches: GeminiCaches,
        *,
        ttl_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._caches = caches
        self._ttl = ttl_seconds
        self._clock = clock
        self._live: dict[str, CreatedCache] = {}
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
            return await self._create(key, prefix)

    async def _create(self, key: str, prefix: CachePrefix) -> CacheHit | None:
        config = types.CreateCachedContentConfig(
            display_name=f"headache-{key[:16]}",
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
        self._live[key] = created
        self.created.append(created)
        return CacheHit(cache.name, write_tokens=created.tokens)

    def _reject(self, key: str, model: str, exc: errors.APIError) -> None:
        message = str(exc).lower()
        if "too small" in message or "min_total_token_count" in message:
            self._rejected_keys.add(key)
        else:
            self._rejected_models.add(model)
        logger.warning("Caché explícita rechazada para %s (%s): se usa la implícita.", model, exc)

    async def aclose(self) -> None:
        """Borra las cachés vivas: el almacenamiento se paga hasta que caducan o se borran."""
        for key, live in list(self._live.items()):
            try:
                await self._caches.delete(name=live.name)
            except errors.APIError as exc:
                logger.warning("No se pudo borrar %s (caducará sola): %s", live.name, exc)
                continue
            live.deleted_at = self._clock()
            del self._live[key]

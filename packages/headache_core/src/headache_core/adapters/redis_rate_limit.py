"""Rate limiting distribuido con Redis (fase 10.2): GCRA en un script Lua atómico.

- Atómico: Redis ejecuta el script entero sin intercalar otros comandos, así que N réplicas
  que piden a la vez nunca conceden más del límite (sin candados ni transacciones).
- El reloj es el de Redis (`TIME`), no el de cada réplica: sin desfases entre máquinas.
- Una clave por usuario o modelo con su TAT y TTL = lo que tarda en llenarse el cubo.
- Todas las claves llevan la misma *hash tag* (`{headache-rl}`): en un Redis en clúster caen en
  el mismo slot, condición para que un script use varias claves. El precio es que todo el rate
  limiting vive en un shard (~100 K operaciones/s, de sobra para un límite por petición).

Equivale a la política `rate-limit-by-key` de APIM o a los *usage plans* de API Gateway, pero
con la clave y el coste que decide la aplicación (usuario, plan, tokens estimados).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Self

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from headache_core.config import ScaleRedisSettings
from headache_core.ports import (
    RateLimitDecision,
    RateLimiterUnavailableError,
    RateLimitRequest,
)

# KEYS: una por petición. ARGV: por petición, periodo, límite y coste.
# Devuelve {índice de la regla que deniega (0 = concedido), retry_after, remaining}.
GCRA_SCRIPT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local new_tats = {}
local denied = 0
local retry = 0
local remaining = -1
for i, key in ipairs(KEYS) do
  local base = (i - 1) * 3
  local period = tonumber(ARGV[base + 1])
  local limit = tonumber(ARGV[base + 2])
  local cost = math.min(tonumber(ARGV[base + 3]), limit)
  local interval = period / limit
  local tat = now
  local stored = redis.call('GET', key)
  if stored then tat = math.max(tonumber(stored), now) end
  local new_tat = tat + cost * interval
  local allow_at = new_tat - period
  if allow_at > now then
    if denied == 0 then denied = i end
    retry = math.max(retry, allow_at - now)
  else
    new_tats[i] = new_tat
    local left = math.floor((now - allow_at) / interval + 1e-9)
    if remaining < 0 or left < remaining then remaining = left end
  end
end
if denied > 0 then
  return {denied, tostring(retry), 0}
end
for i, key in ipairs(KEYS) do
  local ttl = math.max(1, math.ceil((new_tats[i] - now) * 1000))
  redis.call('SET', key, string.format('%.6f', new_tats[i]), 'PX', ttl)
end
return {0, '0', math.max(remaining, 0)}
"""


class RedisRateLimiter:
    """Si Redis falla, deja de intentarlo `cooldown_seconds` (un mini *circuit breaker*): cada
    petici?n no debe pagar un timeout de conexi?n mientras dura la ca?da."""

    def __init__(
        self,
        client: Redis,
        *,
        key_prefix: str = "{headache-rl}",
        cooldown_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._prefix = key_prefix
        self._script = client.register_script(GCRA_SCRIPT)
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._down_until = 0.0

    @classmethod
    def from_settings(
        cls, settings: ScaleRedisSettings, *, key_prefix: str, cooldown_seconds: float = 5.0
    ) -> Self:
        client = Redis(
            host=settings.host,
            port=settings.port,
            db=settings.db,
            password=settings.password.get_secret_value() or None,
            # Un limitador lento es peor que ninguno: si Redis no contesta pronto, el llamante
            # decide (fail-open en el edge) en vez de colgar la petición.
            socket_timeout=settings.timeout_seconds,
            socket_connect_timeout=settings.timeout_seconds,
            # Sin reintentos (redis-py reintenta 3 veces con backoff por defecto: ~7 s medidos
            # con Redis parado). Para un limitador, fallar r?pido y aplicar el plan B.
            retry=Retry(NoBackoff(), 0),
            max_connections=settings.max_connections,
        )
        return cls(client, key_prefix=key_prefix, cooldown_seconds=cooldown_seconds)

    async def acquire(self, requests: Sequence[RateLimitRequest]) -> RateLimitDecision:
        if not requests:
            return RateLimitDecision(True)
        keys = [f"{self._prefix}:{request.key}" for request in requests]
        args: list[float] = []
        for request in requests:
            args += [request.rule.period_seconds, request.rule.limit, request.cost]
        if self._clock() < self._down_until:
            raise RateLimiterUnavailableError("Redis no disponible (en enfriamiento).")
        try:
            denied, retry_after, remaining = await self._script(keys=keys, args=args)
        except RedisError as exc:
            self._down_until = self._clock() + self._cooldown
            raise RateLimiterUnavailableError(str(exc)) from exc
        if int(denied):
            return RateLimitDecision(
                False,
                retry_after=float(retry_after),
                limited_by=requests[int(denied) - 1].rule.name,
            )
        return RateLimitDecision(True, remaining=int(remaining))

    async def aclose(self) -> None:
        await self._client.aclose()

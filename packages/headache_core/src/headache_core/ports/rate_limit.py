"""Rate limiting distribuido (fase 10.2): un contador compartido por todas las réplicas.

Un límite en memoria por proceso no vale con N réplicas (cada una deja pasar el límite
entero). El puerto decide sobre varias claves a la vez y de forma atómica: o se conceden
todas (p. ej., peticiones y tokens de un modelo) o ninguna.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    """`limit` unidades cada `period_seconds`, con ráfaga de hasta `limit` (como un cubo lleno)."""

    name: str
    limit: float
    period_seconds: float

    def __post_init__(self) -> None:
        if self.limit <= 0 or self.period_seconds <= 0:
            raise ValueError("El límite y el periodo deben ser positivos.")


@dataclass(frozen=True, slots=True)
class RateLimitRequest:
    key: str
    rule: RateLimitRule
    cost: float = 1.0


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    # Segundos hasta que la petición cabría (0 si se concede).
    retry_after: float = 0.0
    # Unidades que quedan en la regla más justa tras conceder (0 si se deniega).
    remaining: int = 0
    # Regla que denegó (para cabeceras, métricas y el mensaje al usuario).
    limited_by: str | None = None


class RateLimiterUnavailableError(Exception):
    """El almacén del limitador no responde. Quien llama decide: fail-open o fail-closed."""


class RateLimiterPort(Protocol):
    async def acquire(self, requests: Sequence[RateLimitRequest]) -> RateLimitDecision:
        """Concede todas las peticiones o ninguna (atómico)."""
        ...

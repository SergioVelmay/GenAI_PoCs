"""Control de admisión del borde (fase 10.2): decidir pronto y barato quién entra.

Orden, de lo más barato a lo más caro:
1. *Load shedding* por prioridad (memoria del proceso): si la réplica va llena para el plan del
   llamante, 503 + `Retry-After` sin tocar Redis ni el modelo.
2. Rate limit por usuario y plan (Redis, compartido entre réplicas): 429 + `Retry-After`.

Si Redis no responde, *fail-open* con un limitador local: mejor un límite aproximado por réplica
que tirar el servicio entero porque cae una pieza auxiliar.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

from headache_core.domain.admission import Caller, ClientPlan, has_capacity
from headache_core.ports import (
    RateLimitDecision,
    RateLimiterPort,
    RateLimiterUnavailableError,
    RateLimitRequest,
    RateLimitRule,
)

logger = logging.getLogger(__name__)


class AdmissionRejectedError(Exception):
    """La petición no entra. `status` es el código HTTP que le corresponde."""

    status: int = 503

    def __init__(self, message: str, *, retry_after: float, reason: str) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.reason = reason


class ServiceOverloadedError(AdmissionRejectedError):
    status = 503


class CallerRateLimitedError(AdmissionRejectedError):
    status = 429

    def __init__(self, message: str, *, retry_after: float, rule: RateLimitRule) -> None:
        super().__init__(message, retry_after=retry_after, reason="rate_limited")
        self.rule = rule


@dataclass(frozen=True, slots=True)
class Admission:
    rule: RateLimitRule
    remaining: int


def plan_rules(per_minute: Mapping[ClientPlan, int]) -> dict[ClientPlan, RateLimitRule]:
    return {plan: RateLimitRule(f"user_{plan}", limit, 60.0) for plan, limit in per_minute.items()}


class AdmissionController:
    def __init__(
        self,
        limiter: RateLimiterPort,
        *,
        rules: Mapping[ClientPlan, RateLimitRule],
        capacity: int,
        fallback: RateLimiterPort | None = None,
        overloaded_retry_after: float = 1.0,
    ) -> None:
        if capacity < 1:
            raise ValueError("La capacidad debe ser >= 1.")
        self._limiter = limiter
        self._fallback = fallback
        self._rules = dict(rules)
        self.capacity = capacity
        self._overloaded_retry_after = overloaded_retry_after
        self.in_flight = 0
        self.outcomes: Counter[str] = Counter()

    def bulkhead(self, capacity: int) -> AdmissionController:
        """Otro compartimento con su propia capacidad y el mismo rate limit por usuario.

        Comparten limitador y reglas (el usuario tiene un único cupo), pero no los huecos: una
        ruta lenta que se llena no deja sin sitio a otra barata.
        """
        return AdmissionController(
            self._limiter,
            rules=self._rules,
            capacity=capacity,
            fallback=self._fallback,
            overloaded_retry_after=self._overloaded_retry_after,
        )

    @asynccontextmanager
    async def admit(self, caller: Caller) -> AsyncIterator[Admission]:
        if not has_capacity(caller.plan, in_flight=self.in_flight, capacity=self.capacity):
            self.outcomes["shed"] += 1
            raise ServiceOverloadedError(
                "Mucha demanda ahora mismo.",
                retry_after=self._overloaded_retry_after,
                reason="shed",
            )
        # El hueco se reserva antes del primer `await`: si no, varias peticiones verían el
        # mismo `in_flight` y entrarían todas.
        self.in_flight += 1
        try:
            rule = self._rules[caller.plan]
            decision = await self._acquire(RateLimitRequest(f"user:{caller.key}", rule))
            if not decision.allowed:
                self.outcomes["rate_limited"] += 1
                raise CallerRateLimitedError(
                    "Has hecho demasiadas peticiones.",
                    retry_after=decision.retry_after,
                    rule=rule,
                )
            self.outcomes["admitted"] += 1
            yield Admission(rule, decision.remaining)
        finally:
            self.in_flight -= 1

    async def _acquire(self, request: RateLimitRequest) -> RateLimitDecision:
        try:
            return await self._limiter.acquire([request])
        except RateLimiterUnavailableError:
            self.outcomes["limiter_unavailable"] += 1
            if self._fallback is None:
                raise
            logger.warning("Limitador compartido caído: límite local por réplica.")
            return await self._fallback.acquire([request])

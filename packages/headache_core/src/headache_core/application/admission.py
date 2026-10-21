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

from headache_core.application.telemetry import genai, get_telemetry, metrics
from headache_core.domain.admission import Caller, ClientPlan, has_capacity
from headache_core.ports import (
    RateLimitDecision,
    RateLimiterPort,
    RateLimiterUnavailableError,
    RateLimitRequest,
    RateLimitRule,
    SpanKind,
    SpanPort,
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
        route: str = "ask",
    ) -> None:
        if capacity < 1:
            raise ValueError("La capacidad debe ser >= 1.")
        # Nombre del compartimento (bulkhead): separa en las métricas `/ask` de `/cases`.
        self.route = route
        self._limiter = limiter
        self._fallback = fallback
        self._rules = dict(rules)
        self.capacity = capacity
        self._overloaded_retry_after = overloaded_retry_after
        self.in_flight = 0
        self.outcomes: Counter[str] = Counter()

    def bulkhead(self, capacity: int, *, route: str) -> AdmissionController:
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
            route=route,
        )

    @asynccontextmanager
    async def admit(self, caller: Caller) -> AsyncIterator[Admission]:
        # En el span de la petición: así se filtran trazas por plan y por motivo de rechazo.
        request_span = get_telemetry().current_span()
        request_span.set_attributes(
            {genai.HEADACHE_PLAN: caller.plan.value, genai.HEADACHE_IN_FLIGHT: self.in_flight}
        )
        if not has_capacity(caller.plan, in_flight=self.in_flight, capacity=self.capacity):
            self._decide(request_span, caller, "shed")
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
            with get_telemetry().span(
                "rate_limit",
                kind=SpanKind.CLIENT,
                attributes={"headache.rate_limit.rule": rule.name},
            ):
                decision = await self._acquire(RateLimitRequest(f"user:{caller.key}", rule))
            if not decision.allowed:
                self._decide(request_span, caller, "rate_limited")
                raise CallerRateLimitedError(
                    "Has hecho demasiadas peticiones.",
                    retry_after=decision.retry_after,
                    rule=rule,
                )
            self._decide(request_span, caller, "admitted")
            yield Admission(rule, decision.remaining)
        finally:
            self.in_flight -= 1

    def _decide(self, span: SpanPort, caller: Caller, outcome: str) -> None:
        self.outcomes[outcome] += 1
        span.set_attribute(genai.HEADACHE_ADMISSION, outcome)
        metrics.admission(route=self.route, plan=caller.plan.value, outcome=outcome)

    async def _acquire(self, request: RateLimitRequest) -> RateLimitDecision:
        try:
            return await self._limiter.acquire([request])
        except RateLimiterUnavailableError:
            self.outcomes["limiter_unavailable"] += 1
            if self._fallback is None:
                raise
            logger.warning("Limitador compartido caído: límite local por réplica.")
            get_telemetry().current_span().add_event("rate_limit.fallback")
            return await self._fallback.acquire([request])

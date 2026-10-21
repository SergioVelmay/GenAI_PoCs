"""Quién entra cuando el sistema va justo (fase 10.2). Política de negocio, determinista.

En un pico (una salida a la venta), el tráfico público no puede dejar fuera a operaciones y
soporte: cada plan solo puede ocupar una parte de la capacidad de la réplica. Por encima de su
parte, se rechaza con 503 + `Retry-After` (*load shedding* por prioridad) antes de gastar nada.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from headache_core.domain.retrieval import AccessScope


class ClientPlan(StrEnum):
    ANONYMOUS = "anonymous"
    PUBLIC = "public"
    INTERNAL = "internal"


# Fracción de la capacidad que puede ocupar cada plan. El 20 % de arriba queda para internos.
CAPACITY_SHARE: dict[ClientPlan, float] = {
    ClientPlan.INTERNAL: 1.0,
    ClientPlan.PUBLIC: 0.8,
    ClientPlan.ANONYMOUS: 0.6,
}


@dataclass(frozen=True, slots=True)
class Caller:
    """Quién llama: `key` es estable por usuario (o por IP si es anónimo)."""

    key: str
    plan: ClientPlan


def has_capacity(plan: ClientPlan, *, in_flight: int, capacity: int) -> bool:
    """¿Cabe una petición más de este plan con `in_flight` peticiones ya en curso?"""
    return in_flight < capacity * CAPACITY_SHARE[plan]


def access_scope(plan: ClientPlan) -> AccessScope:
    """Qu? documentos puede ver cada plan: los internos solo el personal de Headache."""
    if plan is ClientPlan.INTERNAL:
        return AccessScope(("public", "internal"))
    return AccessScope(("public",))

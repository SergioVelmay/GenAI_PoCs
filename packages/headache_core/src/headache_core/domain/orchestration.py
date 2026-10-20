"""Orquestación multi-agente: qué especialista atiende cada caso y qué viaja en el traspaso.

El triaje (LLM) solo extrae y clasifica; la ruta la decide esta tabla determinista. Así el
enrutado es auditable, se prueba sin modelo y un prompt injection no puede elegir especialista
más allá de cambiar la intención (y cada especialista solo tiene sus propias tools).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from headache_core.domain.models import SupportIntent

type Urgency = Literal["low", "normal", "high"]

_ORDER_ID = re.compile(r"^ord_[A-Za-z0-9]{8}$")
_LANGUAGE = re.compile(r"^[a-z]{2}$")


class Specialist(StrEnum):
    REFUNDS = "refunds"
    CREATIVE = "creative"
    KNOWLEDGE = "knowledge"


# Total sobre `SupportIntent`: una intención nueva sin ruta rompe un test, no la producción.
ROUTES: Mapping[SupportIntent, Specialist] = MappingProxyType(
    {
        SupportIntent.REFUND_REQUEST: Specialist.REFUNDS,
        # Redacción con plantilla, tono controlado y aprobación humana antes de enviar.
        SupportIntent.COMPLAINT: Specialist.CREATIVE,
        SupportIntent.PARTNERSHIP: Specialist.CREATIVE,
        SupportIntent.EVENT_INFO: Specialist.KNOWLEDGE,
        SupportIntent.LOST_TICKETS: Specialist.KNOWLEDGE,
        SupportIntent.DATE_CHANGE: Specialist.KNOWLEDGE,
    }
)


def route(intent: SupportIntent) -> Specialist:
    return ROUTES[intent]


def resolve_order_id(system: str | None, extracted: str | None) -> str | None:
    """Manda el pedido que conoce el sistema; el que extrae el LLM, solo si su formato es válido."""
    if system:
        return system
    if extracted and _ORDER_ID.match(extracted):
        return extracted
    return None


def resolve_language(detected: str, fallback: str) -> str:
    detected = detected.strip().lower()
    return detected if _LANGUAGE.match(detected) else fallback


class CaseStatus(StrEnum):
    TRIAGED = "triaged"
    PENDING_APPROVAL = "pending_approval"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


_TRANSITIONS: Mapping[CaseStatus, frozenset[CaseStatus]] = MappingProxyType(
    {
        CaseStatus.TRIAGED: frozenset(
            {CaseStatus.PENDING_APPROVAL, CaseStatus.RESOLVED, CaseStatus.ESCALATED}
        ),
        CaseStatus.PENDING_APPROVAL: frozenset({CaseStatus.RESOLVED, CaseStatus.ESCALATED}),
        CaseStatus.RESOLVED: frozenset(),
        CaseStatus.ESCALATED: frozenset(),
    }
)


def can_transition(current: CaseStatus, target: CaseStatus) -> bool:
    """Con entrega at-least-once, un mensaje repetido no puede reabrir un caso cerrado."""
    return target in _TRANSITIONS[current]


def is_terminal(status: CaseStatus) -> bool:
    return not _TRANSITIONS[status]


@dataclass(frozen=True, slots=True)
class CaseEnvelope:
    """Contexto mínimo del traspaso Triaje → especialista.

    No viaja el email original: el especialista recibe hechos estructurados y un resumen, y si
    necesita el texto lo pide por `email_id` (paso por referencia). Menos tokens, menos datos
    personales repetidos en cada prompt y menos superficie para un prompt injection.
    `customer_id` sale del sistema de correo, nunca del LLM.
    """

    case_id: str
    email_id: str
    customer_id: str
    specialist: Specialist
    intent: SupportIntent
    language: str
    urgency: Urgency
    summary: str
    order_id: str | None = None


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    case_id: str
    specialist: Specialist
    status: CaseStatus
    # Respuesta para el cliente (o el borrador pendiente de aprobación).
    reply: str
    guardrails: tuple[str, ...] = ()
    approval_id: str | None = None

    def __post_init__(self) -> None:
        if not can_transition(CaseStatus.TRIAGED, self.status):
            raise ValueError(f"Un caso triado no puede pasar a '{self.status}'.")
        # Tras la revisión, el caso resuelto o escalado conserva la referencia de la aprobación.
        if self.status is CaseStatus.PENDING_APPROVAL and self.approval_id is None:
            raise ValueError("Un caso pendiente de aprobación necesita approval_id.")

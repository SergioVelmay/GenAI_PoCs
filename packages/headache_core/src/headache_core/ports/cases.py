"""Puerto del estado de los casos de soporte en el flujo asíncrono (fase 9.6).

Con entrega at-least-once, el estado del caso es lo que hace idempotentes a los workers: un
mensaje repetido encuentra el caso ya avanzado y no vuelve a llamar al LLM. Las transiciones
son compare-and-set sobre el estado: dos réplicas no pueden cerrar el mismo caso dos veces.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from headache_core.domain import CaseStatus, Specialist
from headache_core.ports.outbox import OutboxEvent


@dataclass(frozen=True, slots=True)
class CaseRecord:
    case_id: str
    email_id: str
    customer_id: str
    # None mientras el caso está reclamado (RECEIVED) y aún no triado.
    specialist: Specialist | None
    status: CaseStatus
    # Traspaso y resultado en el mismo JSON plano que viaja por la cola y por A2A.
    envelope: Mapping[str, Any] | None
    created_at: datetime
    updated_at: datetime
    outcome: Mapping[str, Any] | None = None
    approval_id: str | None = None
    guardrails: tuple[str, ...] = field(default=())


class CaseStore(Protocol):
    """`events` se escriben en el outbox en la misma transacción que el caso (fase 10.5): si el
    cambio no se aplica (conflicto), tampoco se publica nada."""

    async def get(self, case_id: str) -> CaseRecord | None: ...

    async def create(self, record: CaseRecord, *, events: Sequence[OutboxEvent] = ()) -> bool:
        """False si el caso ya existía (otra réplica o una entrega repetida lo creó antes)."""
        ...

    async def transition(
        self,
        case_id: str,
        expected: CaseStatus,
        updated: CaseRecord,
        *,
        events: Sequence[OutboxEvent] = (),
    ) -> bool:
        """Solo actualiza si el estado sigue siendo `expected`."""
        ...

    async def list_cases(
        self, *, status: CaseStatus | None = None, limit: int = 50
    ) -> list[CaseRecord]: ...

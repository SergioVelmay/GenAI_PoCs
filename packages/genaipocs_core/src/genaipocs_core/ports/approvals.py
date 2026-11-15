"""Puerto de solicitudes de aprobación humana (human-in-the-loop, fase 8.5)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    FAILED = "failed"


OPEN_STATUSES = frozenset({ApprovalStatus.PENDING, ApprovalStatus.APPROVED})


def approval_fingerprint(customer_id: str, tool: str, arguments: str) -> str:
    """Identifica la acción exacta: la misma petición repetida reutiliza la solicitud abierta."""
    return hashlib.sha256(f"{customer_id}\n{tool}\n{arguments}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    id: str
    tool: str
    # JSON canónico de los argumentos validados: lo que se aprueba es lo que se ejecuta.
    arguments: str
    customer_id: str
    # Generado por código a partir de los argumentos, nunca por el modelo.
    summary: str
    reason: str
    created_at: datetime
    expires_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    reviewer: str | None = None
    note: str = ""
    decided_at: datetime | None = None
    # Salida de la tool al ejecutarse (o error tipado si falló).
    result: str | None = None

    @property
    def fingerprint(self) -> str:
        return approval_fingerprint(self.customer_id, self.tool, self.arguments)


class ApprovalConflictError(Exception):
    """Ya hay una solicitud abierta para la misma acción."""


class ApprovalStore(Protocol):
    async def add(self, request: ApprovalRequest) -> None: ...

    async def get(self, approval_id: str) -> ApprovalRequest | None: ...

    async def find_open(self, fingerprint: str) -> ApprovalRequest | None: ...

    async def list_requests(
        self,
        *,
        status: ApprovalStatus | None = None,
        customer_id: str | None = None,
        limit: int = 50,
    ) -> list[ApprovalRequest]: ...

    async def transition(
        self, approval_id: str, expected: ApprovalStatus, updated: ApprovalRequest
    ) -> bool:
        """Compare-and-set: solo actualiza si el estado sigue siendo `expected`."""
        ...

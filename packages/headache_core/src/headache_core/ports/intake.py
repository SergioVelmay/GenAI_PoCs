"""Alta idempotente de casos desde la web (fase 10.5): `POST /cases` con `Idempotency-Key`.

El cliente (o un proxy, o el usuario con doble clic) puede repetir la petición. La clave de
idempotencia hace que la repetición devuelva **el mismo caso** en lugar de crear otro:
- Misma clave y mismo cuerpo: la respuesta original (202 con el mismo `case_id`).
- Misma clave y otro cuerpo: 409 (reutilizar una clave para otra cosa es un error del cliente).
La clave es por cliente: dos clientes con la misma clave no chocan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from headache_core.ports.outbox import OutboxEvent


@dataclass(frozen=True, slots=True)
class CaseSubmission:
    customer_id: str
    idempotency_key: str
    # Huella del cuerpo: distingue "la misma petición repetida" de "otra petición, misma clave".
    request_hash: str
    case_id: str
    email_id: str
    created_at: datetime


class CaseIntakeStore(Protocol):
    async def submit(
        self, submission: CaseSubmission, event: OutboxEvent
    ) -> tuple[CaseSubmission, bool]:
        """Guarda el alta y su evento en una transacción. Devuelve la guardada y si es nueva.

        Si la clave ya existe devuelve la original (sin evento nuevo), o `ConflictError` si su
        huella no coincide.
        """
        ...

    async def get_submission(self, case_id: str) -> CaseSubmission | None: ...

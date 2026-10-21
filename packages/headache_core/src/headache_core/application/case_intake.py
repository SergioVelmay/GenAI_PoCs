"""Alta de casos desde la web (fase 10.5): respuesta inmediata, trabajo pesado en la cola.

`POST /cases` no espera al triaje ni a los especialistas (segundos o minutos con LLM): guarda
el alta y el evento `support.emails` en una transacción (outbox) y responde 202 con el
`case_id`. El cliente sigue el caso por SSE. Con `Idempotency-Key`, repetir la petición
devuelve el mismo caso.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from headache_core.application.orchestration import case_id_for, email_to_payload
from headache_core.domain import SupportEmail
from headache_core.domain.semantic_cache import detect_language
from headache_core.ports.intake import CaseIntakeStore, CaseSubmission
from headache_core.ports.outbox import OutboxEvent

SUPPORT_EMAILS = "support.emails"
_KEY = re.compile(r"^[A-Za-z0-9_.:-]{8,200}$")


class InvalidIdempotencyKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CaseRequest:
    subject: str
    body: str
    order_id: str | None = None
    language: str | None = None


@dataclass(frozen=True, slots=True)
class SubmittedCase:
    case_id: str
    # False si la petición repetía una ya aceptada (misma clave y mismo cuerpo).
    created: bool


def request_hash(customer_id: str, request: CaseRequest) -> str:
    canonical = json.dumps(
        {
            "customer_id": customer_id,
            "subject": request.subject,
            "body": request.body,
            "order_id": request.order_id,
            "language": request.language,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class CaseIntakeService:
    def __init__(
        self,
        store: CaseIntakeStore,
        *,
        topic: str = SUPPORT_EMAILS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], str] = lambda: uuid.uuid4().hex[:16],
    ) -> None:
        self._store = store
        self._topic = topic
        self._clock = clock
        self._new_id = new_id

    async def submit(
        self, *, customer_id: str, idempotency_key: str, request: CaseRequest
    ) -> SubmittedCase:
        if not _KEY.match(idempotency_key):
            raise InvalidIdempotencyKeyError(
                "Idempotency-Key: entre 8 y 200 caracteres [A-Za-z0-9_.:-]."
            )
        now = self._clock()
        language = request.language or detect_language(f"{request.subject} {request.body}")
        email = SupportEmail(
            id=f"web_{self._new_id()}",
            customer_id=customer_id,
            language=language if language in ("es", "en") else "es",
            subject=request.subject,
            body=request.body,
            received_at=now,
            order_id=request.order_id,
        )
        case_id = case_id_for(email)
        submission = CaseSubmission(
            customer_id=customer_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash(customer_id, request),
            case_id=case_id,
            email_id=email.id,
            created_at=now,
        )
        event = OutboxEvent(self._topic, {**email_to_payload(email), "correlation_id": case_id})
        stored, created = await self._store.submit(submission, event)
        return SubmittedCase(stored.case_id, created)

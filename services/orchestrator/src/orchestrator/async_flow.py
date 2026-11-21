"""Flujo asíncrono por eventos (coreografía): el email entra en una cola y nadie espera.

    support.emails ──▶ TriageHandler ──▶ case.triaged.<especialista> ──▶ SpecialistHandler
                                                                           │
          CaseReviewService (persona) ◀── pendiente de aprobación ◀───────┤
                     └────────────────────────────▶ case.outcomes ◀────────┘
          ReviewHandler ◀── case.reviews ◀── Workflows (decisión o SLA vencido, 9.G5)

Orquestación (fase 9.3/9.4) frente a coreografía (aquí): en el grafo un proceso conoce el flujo
entero y guarda el estado en checkpoints; aquí cada worker reacciona a un evento, escala por
separado (más réplicas del especialista con más cola) y el estado vive en el `CaseStore`.

Entrega at-least-once ⇒ handlers idempotentes:
- El caso se identifica por el email (`case_<email_id>`) y se reclama (RECEIVED) antes de
  llamar al LLM: un email repetido, aunque llegue a la vez, no se re-triagea.
- Cada worker comprueba el estado antes de trabajar y lo avanza con compare-and-set.
- Guardar y publicar son atómicos (fase 10.5): el evento va al outbox en la misma transacción
  que el cambio de estado y un relay lo publica. Si el caso no avanza (conflicto), no se publica.
- Reintentar un especialista es seguro: las solicitudes de aprobación son idempotentes.
- Mensaje mal formado o traspaso inválido ⇒ `PermanentMessageError` ⇒ DLQ sin reintentos.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from genaipocs_core.application.llm import StructuredOutputError
from genaipocs_core.application.orchestration import (
    InvalidHandoffError,
    SpecialistAgent,
    TriageAgent,
    case_id_for,
    email_from_payload,
    envelope_from_payload,
    envelope_to_payload,
    outcome_from_payload,
    outcome_to_payload,
)
from genaipocs_core.domain import CaseOutcome, CaseStatus, Specialist, can_transition
from genaipocs_core.ports import Message, MessageBusPort, PermanentMessageError
from genaipocs_core.ports.approvals import ApprovalStatus
from genaipocs_core.ports.cases import CaseRecord, CaseStore
from genaipocs_core.ports.outbox import OutboxEvent
from harness.approvals import ApprovalService
from orchestrator.review import ReviewError, apply_review, parse_decision

logger = logging.getLogger(__name__)

SUPPORT_EMAILS = "support.emails"
CASE_OUTCOMES = "case.outcomes"
# 9.G5: decisiones de revisión que llegan por evento (Workflows con callback y SLA).
CASE_REVIEWS = "case.reviews"
REVIEW_DECISION = "decision"
REVIEW_SLA_EXPIRED = "sla_expired"
# Código del guardrail que deja un caso escalado por SLA vencido.
SLA_EXPIRED_CODE = "review_sla_expired"

type Clock = Callable[[], datetime]


def triaged_topic(specialist: Specialist) -> str:
    return f"case.triaged.{specialist.value}"


@dataclass(frozen=True, slots=True)
class CaseTopics:
    """Nombres de las colas. El prefijo aísla entornos (y los tests de integración)."""

    prefix: str = ""

    @property
    def emails(self) -> str:
        return f"{self.prefix}{SUPPORT_EMAILS}"

    @property
    def outcomes(self) -> str:
        return f"{self.prefix}{CASE_OUTCOMES}"

    @property
    def reviews(self) -> str:
        return f"{self.prefix}{CASE_REVIEWS}"

    def triaged(self, specialist: Specialist) -> str:
        return f"{self.prefix}{triaged_topic(specialist)}"


DEFAULT_TOPICS = CaseTopics()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _event(payload: Mapping[str, Any], case_id: str) -> dict[str, Any]:
    # correlation_id = case_id: une en logs y trazas los eventos de un mismo caso.
    return {**payload, "correlation_id": case_id}


class TriageHandler:
    def __init__(
        self,
        triage: TriageAgent,
        cases: CaseStore,
        *,
        topics: CaseTopics = DEFAULT_TOPICS,
        clock: Clock = _utcnow,
    ) -> None:
        self._triage = triage
        self._cases = cases
        self._topics = topics
        self._clock = clock

    async def __call__(self, message: Message) -> None:
        try:
            email = email_from_payload(message.payload)
        except InvalidHandoffError as exc:
            raise PermanentMessageError(str(exc)) from exc
        case_id = case_id_for(email)
        now = self._clock()
        # Se reclama el caso ANTES de llamar al LLM: dos copias del email procesadas a la vez
        # (prefetch > 1 o varias réplicas) no pagan dos triajes. Medido en la integración.
        claim = CaseRecord(
            case_id=case_id,
            email_id=email.id,
            customer_id=email.customer_id,
            specialist=None,
            status=CaseStatus.RECEIVED,
            envelope=None,
            created_at=now,
            updated_at=now,
        )
        if not await self._cases.create(claim):
            existing = await self._cases.get(case_id)
            if existing is None:
                raise RuntimeError(f"Caso {case_id} en conflicto pero no encontrado.")
            # Una primera entrega que encuentra el caso ya creado es una copia del email: el dueño
            # lo termina o, si cae, el broker le reentrega SU mensaje (entrega > 1). Solo esa
            # reentrega retoma un triaje interrumpido. Un caso ya triado no se toca: su traspaso
            # está en el outbox desde la misma transacción (antes había que republicarlo).
            redelivery = message.delivery_count > 1
            if existing.status is not CaseStatus.RECEIVED or not redelivery:
                logger.info("[%s] email duplicado: el caso ya está %s", case_id, existing.status)
                return
            logger.info("[%s] se retoma un triaje interrumpido", case_id)
            claim = existing
        try:
            result = await self._triage.triage(email, case_id=case_id)
        except StructuredOutputError as exc:
            # El modelo no produce un triaje válido tras corregirse: lo mira una persona.
            raise PermanentMessageError(f"Triaje imposible: {exc}") from exc
        envelope = result.envelope
        record = replace(
            claim,
            specialist=envelope.specialist,
            status=CaseStatus.TRIAGED,
            envelope=envelope_to_payload(envelope),
            updated_at=self._clock(),
        )
        handoff = OutboxEvent(
            self._topics.triaged(envelope.specialist), _event(record.envelope or {}, case_id)
        )
        if not await self._cases.transition(case_id, CaseStatus.RECEIVED, record, events=[handoff]):
            logger.info("[%s] otra réplica triagó el caso a la vez: se descarta este", case_id)
            return
        logger.info("[%s] triado: %s → %s", case_id, envelope.intent, envelope.specialist)


class SpecialistHandler:
    def __init__(
        self,
        agent: SpecialistAgent,
        cases: CaseStore,
        *,
        topics: CaseTopics = DEFAULT_TOPICS,
        clock: Clock = _utcnow,
    ) -> None:
        self._agent = agent
        self._cases = cases
        self._topics = topics
        self._clock = clock

    @property
    def topic(self) -> str:
        return self._topics.triaged(self._agent.specialist)

    async def __call__(self, message: Message) -> None:
        try:
            envelope = envelope_from_payload(message.payload)
        except InvalidHandoffError as exc:
            raise PermanentMessageError(str(exc)) from exc
        if envelope.specialist is not self._agent.specialist:
            raise PermanentMessageError(f"Caso para {envelope.specialist} en la cola equivocada.")
        record = await self._cases.get(envelope.case_id)
        if record is None:
            raise PermanentMessageError(f"Caso {envelope.case_id} desconocido.")
        if record.status is not CaseStatus.TRIAGED:
            logger.info("[%s] ya atendido (%s): se ignora", envelope.case_id, record.status)
            return
        # El traspaso que manda es el guardado al triar, no el del mensaje.
        if record.envelope is None or dict(record.envelope) != envelope_to_payload(envelope):
            raise PermanentMessageError(f"El traspaso de {envelope.case_id} no coincide.")
        try:
            outcome = await self._agent.handle(envelope)
        except InvalidHandoffError as exc:
            raise PermanentMessageError(str(exc)) from exc
        # Cualquier otro error (LLM caído, especialista remoto no disponible) es transitorio:
        # se propaga y el broker reintenta con backoff hasta mandarlo a la DLQ.
        logger.info(
            "[%s] %s (entrega %d): %s",
            envelope.case_id,
            self._agent.specialist,
            message.delivery_count,
            outcome.status,
        )
        await close_step(self._cases, record, outcome, self._clock(), self._topics.outcomes)


async def subscribe_workers(
    bus: MessageBusPort,
    cases: CaseStore,
    *,
    triage: TriageAgent | None = None,
    specialists: Mapping[Specialist, SpecialistAgent] | None = None,
    review: CaseReviewService | None = None,
    topics: CaseTopics = DEFAULT_TOPICS,
    clock: Clock = _utcnow,
) -> list[str]:
    """Engancha los roles pedidos a sus colas. Cada rol puede ir en su propio proceso."""
    subscribed: list[str] = []
    if triage is not None:
        handler = TriageHandler(triage, cases, topics=topics, clock=clock)
        await bus.subscribe(topics.emails, handler)
        subscribed.append(topics.emails)
    for agent in (specialists or {}).values():
        worker = SpecialistHandler(agent, cases, topics=topics, clock=clock)
        await bus.subscribe(worker.topic, worker)
        subscribed.append(worker.topic)
    if review is not None:
        await bus.subscribe(topics.reviews, ReviewHandler(review))
        subscribed.append(topics.reviews)
    return subscribed


async def close_step(
    cases: CaseStore,
    record: CaseRecord,
    outcome: CaseOutcome,
    now: datetime,
    topic: str = CASE_OUTCOMES,
) -> bool:
    """Avanza el caso con compare-and-set y deja el resultado en el outbox (misma transacción).
    False si otro llegó antes."""
    if not can_transition(record.status, outcome.status):
        raise PermanentMessageError(f"Transición inválida {record.status} → {outcome.status}.")
    updated = replace(
        record,
        status=outcome.status,
        outcome=outcome_to_payload(outcome),
        approval_id=outcome.approval_id,
        guardrails=outcome.guardrails,
        updated_at=now,
    )
    event = OutboxEvent(topic, _event(updated.outcome or {}, record.case_id))
    if not await cases.transition(record.case_id, record.status, updated, events=[event]):
        logger.info("[%s] otra réplica avanzó el caso antes: se descarta", record.case_id)
        return False
    return True


class CaseReviewService:
    """Revisión humana en el flujo asíncrono: el estado está en el `CaseStore`, no en un grafo."""

    def __init__(
        self,
        cases: CaseStore,
        approvals: ApprovalService,
        *,
        topics: CaseTopics = DEFAULT_TOPICS,
        clock: Clock = _utcnow,
    ) -> None:
        self._cases = cases
        self._approvals = approvals
        self._topics = topics
        self._clock = clock

    async def pending(self, *, limit: int = 50) -> list[CaseRecord]:
        return await self._cases.list_cases(status=CaseStatus.PENDING_APPROVAL, limit=limit)

    async def get(self, case_id: str) -> CaseRecord | None:
        return await self._cases.get(case_id)

    async def review(
        self, case_id: str, *, reviewer: str, approve: bool, note: str = ""
    ) -> CaseRecord:
        record = await self._cases.get(case_id)
        if record is None or record.status is not CaseStatus.PENDING_APPROVAL:
            raise ReviewError(f"El caso {case_id} no espera revisión.")
        decision = parse_decision(
            {"reviewer": reviewer, "approve": approve, "note": note}, record.customer_id
        )
        assert record.outcome is not None
        outcome = outcome_from_payload(record.outcome)
        reviewed = await apply_review(self._approvals, outcome, decision)
        now = self._clock()
        topic = self._topics.outcomes
        if not await close_step(self._cases, record, reviewed, now, topic):
            raise ReviewError(f"Otra persona ya ha revisado el caso {case_id}.")
        current = await self._cases.get(case_id)
        assert current is not None
        return current

    async def expire(self, case_id: str, *, reason: str = SLA_EXPIRED_CODE) -> CaseRecord:
        """SLA de revisión vencido (9.G5): la solicitud caduca sin ejecutarse y el caso se escala.

        Lo decide el dominio, no Workflows (que solo avisa del plazo). Idempotente: un caso que
        ya no espera revisión se devuelve tal cual; si alguien decidió justo antes del plazo, su
        decisión manda y el caso lo cierra su revisión.
        """
        record = await self._cases.get(case_id)
        if record is None:
            raise ReviewError(f"El caso {case_id} no existe.")
        if record.status is not CaseStatus.PENDING_APPROVAL or record.outcome is None:
            return record
        outcome = outcome_from_payload(record.outcome)
        if outcome.approval_id is not None:
            request = await self._approvals.expire(outcome.approval_id, reason=reason)
            if request.status is not ApprovalStatus.EXPIRED:
                logger.info("[%s] decidido antes del SLA (%s)", case_id, request.status)
                return record
        escalated = replace(
            outcome, status=CaseStatus.ESCALATED, guardrails=(*outcome.guardrails, reason)
        )
        await close_step(self._cases, record, escalated, self._clock(), self._topics.outcomes)
        current = await self._cases.get(case_id)
        assert current is not None
        return current


class ReviewHandler:
    """Decisiones de revisión que llegan por evento (`case.reviews`, publicadas por Workflows).

    `{"kind": "decision", "case_id", "reviewer", "approve", "note"}` o
    `{"kind": "sla_expired", "case_id"}`. Idempotente: un caso ya revisado o escalado se
    descarta (Workflows y Pub/Sub entregan al menos una vez). Una decisión inválida (p. ej. el
    propio cliente como revisor) va a la DLQ: no se reintenta.
    """

    def __init__(self, review: CaseReviewService) -> None:
        self._review = review

    async def __call__(self, message: Message) -> None:
        payload = message.payload
        case_id = payload.get("case_id")
        kind = payload.get("kind")
        if not isinstance(case_id, str) or not case_id:
            raise PermanentMessageError("Revisión sin case_id.")
        if kind == REVIEW_SLA_EXPIRED:
            record = await self._review.expire(case_id)
            logger.info("[%s] SLA de revisión vencido: %s", case_id, record.status)
            return
        if kind != REVIEW_DECISION:
            raise PermanentMessageError(f"Tipo de revisión desconocido: {kind!r}.")
        reviewer, approve = payload.get("reviewer"), payload.get("approve")
        if not isinstance(reviewer, str) or not isinstance(approve, bool):
            raise PermanentMessageError("La decisión necesita reviewer (texto) y approve.")
        try:
            record = await self._review.review(
                case_id, reviewer=reviewer, approve=approve, note=str(payload.get("note", ""))
            )
        except ReviewError as exc:
            current = await self._review.get(case_id)
            if current is not None and current.status is not CaseStatus.PENDING_APPROVAL:
                logger.info("[%s] ya revisado (%s): se descarta", case_id, current.status)
                return
            raise PermanentMessageError(str(exc)) from exc
        logger.info("[%s] revisado por %s: %s", case_id, reviewer, record.status)

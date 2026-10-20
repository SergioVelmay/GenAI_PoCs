"""Flujo asíncrono por eventos (coreografía): el email entra en una cola y nadie espera.

    support.emails ──▶ TriageHandler ──▶ case.triaged.<especialista> ──▶ SpecialistHandler
                                                                           │
          CaseReviewService (persona) ◀── pendiente de aprobación ◀───────┤
                     └────────────────────────────▶ case.outcomes ◀────────┘

Orquestación (fase 9.3/9.4) frente a coreografía (aquí): en el grafo un proceso conoce el flujo
entero y guarda el estado en checkpoints; aquí cada worker reacciona a un evento, escala por
separado (más réplicas del especialista con más cola) y el estado vive en el `CaseStore`.

Entrega at-least-once ⇒ handlers idempotentes:
- El caso se identifica por el email (`case_<email_id>`) y se reclama (RECEIVED) antes de
  llamar al LLM: un email repetido, aunque llegue a la vez, no se re-triagea.
- Cada worker comprueba el estado antes de trabajar y lo avanza con compare-and-set.
- Si el triaje cae entre guardar y publicar, la reentrega ve el caso TRIAGED y solo republica.
- Reintentar un especialista es seguro: las solicitudes de aprobación son idempotentes.
- Mensaje mal formado o traspaso inválido ⇒ `PermanentMessageError` ⇒ DLQ sin reintentos.
Límite conocido: guardar y publicar no son atómicos; el patrón *transactional outbox* lo cierra.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from harness.approvals import ApprovalService
from headache_core.application.llm import StructuredOutputError
from headache_core.application.orchestration import (
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
from headache_core.domain import CaseOutcome, CaseStatus, Specialist, can_transition
from headache_core.ports import Message, MessageBusPort, PermanentMessageError
from headache_core.ports.cases import CaseRecord, CaseStore
from orchestrator.review import ReviewError, apply_review, parse_decision

logger = logging.getLogger(__name__)

SUPPORT_EMAILS = "support.emails"
CASE_OUTCOMES = "case.outcomes"

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
        bus: MessageBusPort,
        *,
        topics: CaseTopics = DEFAULT_TOPICS,
        clock: Clock = _utcnow,
    ) -> None:
        self._triage = triage
        self._cases = cases
        self._bus = bus
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
            # reentrega retoma el trabajo; si una copia republicara, el especialista correría dos
            # veces (visto en la CI con RabbitMQ).
            redelivery = message.delivery_count > 1
            if existing.status is CaseStatus.TRIAGED and redelivery:
                # El triaje anterior guardó el caso pero quizá no llegó a publicarlo.
                logger.info("[%s] reentrega: se republica el traspaso sin re-triagear", case_id)
                await self._publish(existing)
                return
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
        if not await self._cases.transition(case_id, CaseStatus.RECEIVED, record):
            logger.info("[%s] otra réplica triagó el caso a la vez: se descarta este", case_id)
            return
        logger.info("[%s] triado: %s → %s", case_id, envelope.intent, envelope.specialist)
        await self._publish(record)

    async def _publish(self, record: CaseRecord) -> None:
        assert record.specialist is not None and record.envelope is not None
        await self._bus.publish(
            self._topics.triaged(record.specialist), _event(record.envelope, record.case_id)
        )


class SpecialistHandler:
    def __init__(
        self,
        agent: SpecialistAgent,
        cases: CaseStore,
        bus: MessageBusPort,
        *,
        topics: CaseTopics = DEFAULT_TOPICS,
        clock: Clock = _utcnow,
    ) -> None:
        self._agent = agent
        self._cases = cases
        self._bus = bus
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
        await close_step(
            self._cases, self._bus, record, outcome, self._clock(), self._topics.outcomes
        )


async def subscribe_workers(
    bus: MessageBusPort,
    cases: CaseStore,
    *,
    triage: TriageAgent | None = None,
    specialists: Mapping[Specialist, SpecialistAgent] | None = None,
    topics: CaseTopics = DEFAULT_TOPICS,
    clock: Clock = _utcnow,
) -> list[str]:
    """Engancha los roles pedidos a sus colas. Cada rol puede ir en su propio proceso."""
    subscribed: list[str] = []
    if triage is not None:
        handler = TriageHandler(triage, cases, bus, topics=topics, clock=clock)
        await bus.subscribe(topics.emails, handler)
        subscribed.append(topics.emails)
    for agent in (specialists or {}).values():
        worker = SpecialistHandler(agent, cases, bus, topics=topics, clock=clock)
        await bus.subscribe(worker.topic, worker)
        subscribed.append(worker.topic)
    return subscribed


async def close_step(
    cases: CaseStore,
    bus: MessageBusPort,
    record: CaseRecord,
    outcome: CaseOutcome,
    now: datetime,
    topic: str = CASE_OUTCOMES,
) -> bool:
    """Avanza el caso con compare-and-set y publica el resultado. False si otro llegó antes."""
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
    if not await cases.transition(record.case_id, record.status, updated):
        logger.info("[%s] otra réplica avanzó el caso antes: se descarta", record.case_id)
        return False
    await bus.publish(topic, _event(updated.outcome or {}, record.case_id))
    return True


class CaseReviewService:
    """Revisión humana en el flujo asíncrono: el estado está en el `CaseStore`, no en un grafo."""

    def __init__(
        self,
        cases: CaseStore,
        approvals: ApprovalService,
        bus: MessageBusPort,
        *,
        topics: CaseTopics = DEFAULT_TOPICS,
        clock: Clock = _utcnow,
    ) -> None:
        self._cases = cases
        self._approvals = approvals
        self._bus = bus
        self._topics = topics
        self._clock = clock

    async def pending(self, *, limit: int = 50) -> list[CaseRecord]:
        return await self._cases.list_cases(status=CaseStatus.PENDING_APPROVAL, limit=limit)

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
        if not await close_step(self._cases, self._bus, record, reviewed, now, topic):
            raise ReviewError(f"Otra persona ya ha revisado el caso {case_id}.")
        current = await self._cases.get(case_id)
        assert current is not None
        return current

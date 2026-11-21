"""Revisión por evento (9.G5): `case.reviews` con la decisión de una persona o el SLA vencido.

Workflows (`infra/workflows/case_review.yaml`) espera la decisión con un callback y un plazo, y
publica en `case.reviews`; `ReviewHandler` la aplica con `CaseReviewService`. Aquí, con el bus
en memoria y sin nube.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from genaipocs_core.adapters.memory import InMemoryCaseStore, InMemoryMessageBus, InMemoryOutbox
from genaipocs_core.application.orchestration import TriageAgent, email_to_payload
from genaipocs_core.domain import CaseStatus, OrderStatus
from genaipocs_core.ports import ToolCall
from genaipocs_core.ports.approvals import ApprovalStatus
from orchestrator.async_flow import (
    CASE_OUTCOMES,
    CASE_REVIEWS,
    REVIEW_DECISION,
    REVIEW_SLA_EXPIRED,
    SLA_EXPIRED_CODE,
    SUPPORT_EMAILS,
    CaseReviewService,
    subscribe_workers,
)
from orchestrator.specialists import build_approval_service, build_specialists
from orchestrator.testing import CUSTOMER, ORDER, LocalWorld, local_world, support_email

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PENDING = "Tu solicitud queda pendiente de revisión por un agente de Genaipocs."
CASE = "case_eml_1"


async def _pending_case() -> tuple[LocalWorld, InMemoryMessageBus, InMemoryCaseStore]:
    triage = {
        "intent": "refund_request",
        "language": "es",
        "order_id": ORDER,
        "urgency": "normal",
        "summary": "Pide el reembolso.",
    }
    refund = json.dumps({"order_id": ORDER, "reason": "No puede asistir."})
    world = local_world([json.dumps(triage), [ToolCall("c1", "request_refund", refund)], PENDING])
    bus = InMemoryMessageBus(max_deliveries=2)
    cases = InMemoryCaseStore(InMemoryOutbox(deliver_to=bus))
    review = CaseReviewService(cases, build_approval_service(world.deps), clock=lambda: NOW)
    topics = await subscribe_workers(
        bus,
        cases,
        triage=TriageAgent(world.llm),
        specialists=build_specialists(world.deps),
        review=review,
        clock=lambda: NOW,
    )
    assert CASE_REVIEWS in topics
    await bus.publish(SUPPORT_EMAILS, email_to_payload(support_email("Devolvedme el dinero.")))
    case = await cases.get(CASE)
    assert case is not None and case.status is CaseStatus.PENDING_APPROVAL
    return world, bus, cases


def _decision(reviewer: str = "ops-1", *, approve: bool = True) -> dict[str, object]:
    return {"kind": REVIEW_DECISION, "case_id": CASE, "reviewer": reviewer, "approve": approve}


def _statuses(bus: InMemoryMessageBus) -> list[object]:
    return [m.payload["status"] for m in bus.published if m.topic == CASE_OUTCOMES]


async def test_decision_from_workflows_resolves_the_case() -> None:
    world, bus, cases = await _pending_case()
    calls = len(world.llm.calls)

    await bus.publish(CASE_REVIEWS, _decision())

    case = await cases.get(CASE)
    assert case is not None and case.status is CaseStatus.RESOLVED
    [request] = await world.approvals.list_requests()
    assert request.status is ApprovalStatus.EXECUTED and request.reviewer == "ops-1"
    assert len(world.llm.calls) == calls  # Revisar no llama al modelo.
    assert _statuses(bus) == ["pending_approval", "resolved"]


async def test_expired_sla_escalates_and_the_refund_is_never_executed() -> None:
    world, bus, cases = await _pending_case()

    await bus.publish(CASE_REVIEWS, {"kind": REVIEW_SLA_EXPIRED, "case_id": CASE})

    case = await cases.get(CASE)
    assert case is not None and case.status is CaseStatus.ESCALATED
    assert SLA_EXPIRED_CODE in case.guardrails
    [request] = await world.approvals.list_requests()
    assert request.status is ApprovalStatus.EXPIRED and request.note == SLA_EXPIRED_CODE
    order = await world.deps.ticketing.get_order(ORDER)
    assert order.status is not OrderStatus.REFUNDED
    assert _statuses(bus) == ["pending_approval", "escalated"]


async def test_a_late_decision_after_the_sla_is_discarded() -> None:
    world, bus, cases = await _pending_case()
    await bus.publish(CASE_REVIEWS, {"kind": REVIEW_SLA_EXPIRED, "case_id": CASE})

    await bus.publish(CASE_REVIEWS, _decision())

    case = await cases.get(CASE)
    assert case is not None and case.status is CaseStatus.ESCALATED
    assert bus.dead_letters == []  # Llegar tarde no es un error: se descarta.
    [request] = await world.approvals.list_requests()
    assert request.status is ApprovalStatus.EXPIRED


async def test_a_decision_just_before_the_sla_wins() -> None:
    _, bus, cases = await _pending_case()
    await bus.publish(CASE_REVIEWS, _decision())

    await bus.publish(CASE_REVIEWS, {"kind": REVIEW_SLA_EXPIRED, "case_id": CASE})

    case = await cases.get(CASE)
    assert case is not None and case.status is CaseStatus.RESOLVED
    assert _statuses(bus) == ["pending_approval", "resolved"]


async def test_duplicate_decision_is_idempotent() -> None:
    world, bus, _ = await _pending_case()

    await bus.publish(CASE_REVIEWS, _decision())
    await bus.publish(CASE_REVIEWS, _decision("ops-2"))

    assert bus.dead_letters == []
    [request] = await world.approvals.list_requests()  # Una sola solicitud, ejecutada una vez.
    assert request.reviewer == "ops-1" and request.status is ApprovalStatus.EXECUTED
    order = await world.deps.ticketing.get_order(ORDER)
    assert order.status is OrderStatus.REFUNDED


async def test_invalid_or_malformed_reviews_go_to_the_dlq() -> None:
    world, bus, cases = await _pending_case()

    await bus.publish(CASE_REVIEWS, _decision(CUSTOMER))  # El cliente no se aprueba a sí mismo.
    await bus.publish(CASE_REVIEWS, {"kind": "approve_everything", "case_id": CASE})
    await bus.publish(CASE_REVIEWS, {"kind": REVIEW_DECISION, "case_id": CASE, "approve": "sí"})
    await bus.publish(CASE_REVIEWS, {"kind": REVIEW_SLA_EXPIRED})

    assert len(bus.dead_letters) == 4
    case = await cases.get(CASE)
    assert case is not None and case.status is CaseStatus.PENDING_APPROVAL
    [request] = await world.approvals.list_requests()
    assert request.status is ApprovalStatus.PENDING

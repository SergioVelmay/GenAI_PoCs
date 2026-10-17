from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from harness import (
    APPROVAL_REQUIRED,
    ApprovalError,
    ApprovalService,
    CorrectionResult,
    GuardedExecutor,
    TurnRecord,
    turn_trace,
)
from harness.rules.partner_email import (
    PartnerEmailCase,
    PartnerEmailDraft,
    PartnerEmailResult,
    RenderedEmail,
    SendPartnerEmailArgs,
    build_partner_email_tools,
    submit_partner_email,
)
from harness.rules.refunds import refund_approval_gate, refund_claims_grounded
from headache_core.adapters.memory import InMemoryApprovalStore, InMemoryUnitOfWork
from headache_core.adapters.ticketing_local import UnitOfWorkTicketing
from headache_core.application.tools import ToolContext, ToolExecution, build_ticketing_tools
from headache_core.domain import Customer, Event, EventCategory, Money, Order, OrderStatus, Session
from headache_core.ports import ToolCall
from headache_core.ports.approvals import ApprovalStatus

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PRICE = Money(Decimal("30.00"), "EUR")
ANA = ToolContext(customer_id="cus_1")
REFUND = ToolCall("c1", "request_refund", json.dumps({"order_id": "ord_1", "reason": "No voy."}))


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


type World = tuple[
    GuardedExecutor, ApprovalService, InMemoryApprovalStore, InMemoryUnitOfWork, Clock
]


def _world() -> World:
    uow = InMemoryUnitOfWork(
        customers=[Customer("cus_1", "Ana Pérez", "ana@example.com", "ES", "es")],
        events=[
            Event(
                "evt_1",
                "Candlelit Strings",
                EventCategory.CANDLELIT,
                "Madrid",
                "ven_1",
                "Velas.",
                PRICE,
                refundable=True,
                refund_window_hours=48,
            )
        ],
        sessions=[Session("ses_1", "evt_1", datetime(2026, 11, 20, 20, tzinfo=UTC), 100, 3)],
        orders=[Order("ord_1", "cus_1", "ses_1", 2, PRICE * 2, OrderStatus.PAID, NOW)],
    )
    tools = build_ticketing_tools(UnitOfWorkTicketing(lambda: uow, clock=lambda: NOW))
    store = InMemoryApprovalStore()
    clock = Clock()
    executor = GuardedExecutor(tools, [replace(refund_approval_gate(store), clock=clock)])
    return executor, ApprovalService(store, tools, clock=clock), store, uow, clock


async def test_irreversible_tool_is_parked_until_a_human_decides() -> None:
    executor, service, store, uow, _ = _world()

    with turn_trace() as trace:
        first = await executor.execute(REFUND, ANA)
        again = await executor.execute(REFUND, ANA)

    assert first.error == again.error == APPROVAL_REQUIRED
    assert uow.refunds.items == []
    [pending] = await service.pending()
    assert pending.id in first.output and pending.id in again.output
    assert pending.customer_id == "cus_1" and pending.status is ApprovalStatus.PENDING
    assert pending.summary.startswith("Reembolso del pedido ord_1.")
    assert "«No voy.»" in pending.summary
    assert pending.expires_at == NOW + timedelta(hours=24)
    assert trace.findings == ["approval_requested", "approval_requested"]
    assert len(store.items) == 1


async def test_approval_executes_exactly_the_frozen_arguments_once() -> None:
    executor, service, _, uow, _ = _world()
    await executor.execute(REFUND, ANA)
    [pending] = await service.pending()

    decided = await service.decide(pending.id, reviewer="ops-1", approve=True, note="OK")

    assert decided.status is ApprovalStatus.EXECUTED and decided.reviewer == "ops-1"
    assert decided.result is not None and json.loads(decided.result)["approved"] is True
    [refund] = uow.refunds.items
    assert refund.order_id == "ord_1" and refund.reason == "No voy."
    with pytest.raises(ApprovalError, match="estado 'executed'"):
        await service.decide(pending.id, reviewer="ops-2", approve=True)


async def test_concurrent_reviewers_cannot_execute_twice() -> None:
    executor, service, _, uow, _ = _world()
    await executor.execute(REFUND, ANA)
    [pending] = await service.pending()

    results = await asyncio.gather(
        service.decide(pending.id, reviewer="ops-1", approve=True),
        service.decide(pending.id, reviewer="ops-2", approve=True),
        return_exceptions=True,
    )

    assert sum(isinstance(r, ApprovalError) for r in results) == 1
    assert len(uow.refunds.items) == 1


async def test_rejection_and_expiry_never_execute() -> None:
    executor, service, store, uow, clock = _world()
    await executor.execute(REFUND, ANA)
    [pending] = await service.pending()

    rejected = await service.decide(pending.id, reviewer="ops-1", approve=False, note="Fraude")

    assert rejected.status is ApprovalStatus.REJECTED and rejected.result is None
    # Rechazada, la acción vuelve a poder solicitarse (nueva solicitud, no la misma).
    await executor.execute(REFUND, ANA)
    [second] = await service.pending()
    assert second.id != pending.id
    clock.now = NOW + timedelta(hours=25)
    with pytest.raises(ApprovalError, match="caducado"):
        await service.decide(second.id, reviewer="ops-1", approve=True)
    assert store.items[second.id].status is ApprovalStatus.EXPIRED
    assert uow.refunds.items == []


async def test_requester_cannot_approve_their_own_action() -> None:
    executor, service, _, _, _ = _world()
    await executor.execute(REFUND, ANA)
    [pending] = await service.pending()

    for reviewer in ("cus_1", " "):
        with pytest.raises(ApprovalError, match="distinta"):
            await service.decide(pending.id, reviewer=reviewer, approve=True)


async def test_partner_email_is_sent_only_after_four_eyes_approval() -> None:
    sent: list[SendPartnerEmailArgs] = []

    async def send(args: SendPartnerEmailArgs) -> str:
        sent.append(args)
        return "msg-1"

    store = InMemoryApprovalStore()
    service = ApprovalService(store, build_partner_email_tools(send))
    case = PartnerEmailCase("INC-1", "Sala Aurora", "Laura", "es", facts={})
    email = RenderedEmail("[Headache Partners] Retraso · INC-1", "Hola, Laura:\n\n...")
    no_draft = CorrectionResult[PartnerEmailDraft](None)
    result = PartnerEmailResult(no_draft, email)

    request = await submit_partner_email(
        service, result, case, to="laura@sala-aurora.example", requested_by="emp_7"
    )

    assert sent == [] and request.summary.startswith("Email a Sala Aurora")
    with pytest.raises(ApprovalError):
        await service.decide(request.id, reviewer="emp_7", approve=True)
    decided = await service.decide(request.id, reviewer="emp_9", approve=True)
    assert decided.status is ApprovalStatus.EXECUTED
    assert [a.subject for a in sent] == [email.subject]
    with pytest.raises(ValueError):
        await submit_partner_email(
            service, PartnerEmailResult(no_draft, None), case, to="x@y.ex", requested_by="emp_7"
        )


def test_refund_claim_needs_an_approved_refund_with_the_same_amount() -> None:
    approved = json.dumps({"approved": True, "amount": "60.00 EUR"})

    def record(answer: str) -> TurnRecord:
        return TurnRecord("x", answer, (ToolExecution(REFUND, approved, None, 1.0),))

    assert refund_claims_grounded(record("Reembolso aprobado: 60,00 €."), ANA) == []
    [violation] = refund_claims_grounded(record("Reembolso aprobado: 75,00 €."), ANA)
    assert violation.code == "ungrounded_amount"

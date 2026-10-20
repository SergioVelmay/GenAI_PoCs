"""Mundo local en memoria para tests y demos sin coste: un cliente, un evento y un pedido."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from harness.rules.case_reply import SendCaseReplyArgs
from headache_core.adapters.local_retrieval import LocalRetrieval
from headache_core.adapters.memory import (
    FakeLLM,
    HashingEmbedding,
    InMemoryApprovalStore,
    InMemoryCheckpointStore,
    InMemoryPreferenceStore,
    InMemoryUnitOfWork,
)
from headache_core.adapters.ticketing_local import UnitOfWorkTicketing
from headache_core.application.retrieval import RetrievalService
from headache_core.application.tools import ToolContext
from headache_core.domain import (
    Customer,
    Event,
    EventCategory,
    Money,
    Order,
    OrderStatus,
    Session,
    SupportEmail,
)
from headache_core.ports import ToolCall
from orchestrator.specialists import SpecialistDeps

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PRICE = Money(Decimal("30.00"), "EUR")
CUSTOMER = "cus_1"
ORDER = "ord_00000001"


@dataclass
class Outbox:
    """Buzón de salida falso: registra lo que se habría enviado."""

    sent: list[tuple[SendCaseReplyArgs, str]] = field(default_factory=list)

    async def send(self, args: SendCaseReplyArgs, context: ToolContext) -> str:
        self.sent.append((args, context.customer_id))
        return f"msg_{len(self.sent)}"


@dataclass
class LocalWorld:
    llm: FakeLLM
    approvals: InMemoryApprovalStore
    outbox: Outbox
    deps: SpecialistDeps


def local_world(
    script: Sequence[str | list[ToolCall] | Exception] = (), *, flavor: str = "native"
) -> LocalWorld:
    uow = InMemoryUnitOfWork(
        customers=[Customer(CUSTOMER, "Ana Pérez", "ana@example.com", "ES", "es")],
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
        orders=[Order(ORDER, CUSTOMER, "ses_1", 2, PRICE * 2, OrderStatus.PAID, NOW)],
    )
    llm, approvals, outbox = FakeLLM(script), InMemoryApprovalStore(), Outbox()
    deps = SpecialistDeps(
        llm=llm,
        ticketing=UnitOfWorkTicketing(lambda: uow, clock=lambda: NOW),
        retrieval=RetrievalService(LocalRetrieval([], embeddings=HashingEmbedding(64))),
        preferences=InMemoryPreferenceStore(),
        checkpoints=InMemoryCheckpointStore(),
        approvals=approvals,
        send_reply=outbox.send,
        flavor=flavor,
    )
    return LocalWorld(llm, approvals, outbox, deps)


def support_email(
    body: str, *, email_id: str = "eml_1", order_id: str | None = ORDER, subject: str = "Ayuda"
) -> SupportEmail:
    return SupportEmail(
        id=email_id,
        customer_id=CUSTOMER,
        language="es",
        subject=subject,
        body=body,
        received_at=NOW,
        order_id=order_id,
    )

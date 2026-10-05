from __future__ import annotations

from datetime import timedelta

import pytest

from headache_core.adapters.memory import (
    InMemoryEventRepository,
    InMemoryOrderRepository,
    InMemorySessionRepository,
)
from headache_core.application import NotFoundError, RefundService
from headache_core.domain import Event, Order, OrderStatus, Session


@pytest.fixture
def orders(order: Order) -> InMemoryOrderRepository:
    return InMemoryOrderRepository([order])


@pytest.fixture
def service(orders: InMemoryOrderRepository, session: Session, event: Event) -> RefundService:
    return RefundService(
        orders=orders,
        sessions=InMemorySessionRepository([session]),
        events=InMemoryEventRepository([event]),
    )


async def test_approved_refund_updates_order(
    service: RefundService, orders: InMemoryOrderRepository, session: Session
) -> None:
    decision = await service.request_refund(
        "ord_1", requested_at=session.starts_at - timedelta(days=5)
    )

    assert decision.approved
    stored = await orders.get("ord_1")
    assert stored is not None
    assert stored.status is OrderStatus.REFUNDED


async def test_rejected_refund_keeps_order(
    service: RefundService, orders: InMemoryOrderRepository, session: Session
) -> None:
    decision = await service.request_refund("ord_1", requested_at=session.starts_at)

    assert not decision.approved
    stored = await orders.get("ord_1")
    assert stored is not None
    assert stored.status is OrderStatus.PAID


async def test_unknown_order_raises(service: RefundService) -> None:
    with pytest.raises(NotFoundError):
        await service.request_refund("no-existe")

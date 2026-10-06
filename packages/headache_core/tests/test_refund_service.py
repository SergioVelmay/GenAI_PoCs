from __future__ import annotations

from datetime import timedelta

import pytest

from headache_core.adapters.memory import InMemoryUnitOfWork
from headache_core.application import NotFoundError, RefundService
from headache_core.domain import Event, Order, OrderStatus, RefundStatus, Session


@pytest.fixture
def uow(order: Order, session: Session, event: Event) -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(events=[event], sessions=[session], orders=[order])


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> RefundService:
    return RefundService(uow)


async def test_approved_refund_updates_order_and_releases_seats(
    service: RefundService, uow: InMemoryUnitOfWork, session: Session, order: Order
) -> None:
    outcome = await service.request_refund(
        "ord_1", reason="Enfermedad", requested_at=session.starts_at - timedelta(days=5)
    )

    assert outcome.decision.approved
    assert outcome.refund.status is RefundStatus.APPROVED
    assert outcome.refund.amount == order.total
    stored = await uow.orders.get("ord_1")
    assert stored is not None
    assert stored.status is OrderStatus.REFUNDED
    released = await uow.sessions.get(session.id)
    assert released is not None
    assert released.available == session.available + order.quantity
    assert uow.commits == 1


async def test_rejected_refund_is_recorded_and_keeps_order(
    service: RefundService, uow: InMemoryUnitOfWork, session: Session
) -> None:
    outcome = await service.request_refund("ord_1", requested_at=session.starts_at)

    assert not outcome.decision.approved
    assert outcome.refund.status is RefundStatus.REJECTED
    assert uow.refunds.items == [outcome.refund]
    stored = await uow.orders.get("ord_1")
    assert stored is not None
    assert stored.status is OrderStatus.PAID


async def test_second_refund_is_rejected(service: RefundService, session: Session) -> None:
    early = session.starts_at - timedelta(days=5)
    await service.request_refund("ord_1", requested_at=early)

    second = await service.request_refund("ord_1", requested_at=early)

    assert not second.decision.approved


async def test_unknown_order_raises(service: RefundService) -> None:
    with pytest.raises(NotFoundError):
        await service.request_refund("no-existe")

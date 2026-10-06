from __future__ import annotations

from datetime import timedelta

import pytest

from headache_core.adapters.memory import InMemoryUnitOfWork
from headache_core.application import NotFoundError, PurchaseService
from headache_core.domain import (
    Customer,
    Event,
    OrderStatus,
    PurchaseNotAllowedError,
    Session,
)
from headache_core.ports import ConflictError


@pytest.fixture
def uow(customer: Customer, event: Event, session: Session) -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(customers=[customer], events=[event], sessions=[session])


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> PurchaseService:
    return PurchaseService(uow)


async def test_purchase_reserves_seats_and_prices_order(
    service: PurchaseService, uow: InMemoryUnitOfWork, session: Session, event: Event
) -> None:
    order = await service.purchase(
        customer_id="cus_1",
        session_id=session.id,
        quantity=3,
        now=session.starts_at - timedelta(days=1),
    )

    assert order.status is OrderStatus.PAID
    assert order.total == event.price * 3
    stored = await uow.sessions.get(session.id)
    assert stored is not None
    assert stored.available == session.available - 3
    assert uow.commits == 1


async def test_purchase_is_idempotent(
    service: PurchaseService, uow: InMemoryUnitOfWork, session: Session
) -> None:
    now = session.starts_at - timedelta(days=1)
    first = await service.purchase(
        customer_id="cus_1", session_id=session.id, quantity=2, idempotency_key="k1", now=now
    )
    retry = await service.purchase(
        customer_id="cus_1", session_id=session.id, quantity=2, idempotency_key="k1", now=now
    )

    assert retry == first
    stored = await uow.sessions.get(session.id)
    assert stored is not None
    assert stored.available == session.available - 2


async def test_reused_idempotency_key_with_other_payload_conflicts(
    service: PurchaseService, session: Session
) -> None:
    now = session.starts_at - timedelta(days=1)
    await service.purchase(
        customer_id="cus_1", session_id=session.id, quantity=2, idempotency_key="k1", now=now
    )

    with pytest.raises(ConflictError):
        await service.purchase(
            customer_id="cus_1", session_id=session.id, quantity=5, idempotency_key="k1", now=now
        )


@pytest.mark.parametrize("quantity", [0, 11])
async def test_quantity_out_of_range_is_rejected(
    service: PurchaseService, session: Session, quantity: int
) -> None:
    with pytest.raises(PurchaseNotAllowedError):
        await service.purchase(
            customer_id="cus_1",
            session_id=session.id,
            quantity=quantity,
            now=session.starts_at - timedelta(days=1),
        )


async def test_cannot_oversell(service: PurchaseService, session: Session) -> None:
    now = session.starts_at - timedelta(days=1)
    for _ in range(session.available // 10):
        await service.purchase(customer_id="cus_1", session_id=session.id, quantity=10, now=now)

    with pytest.raises(PurchaseNotAllowedError):
        await service.purchase(customer_id="cus_1", session_id=session.id, quantity=1, now=now)


async def test_cannot_buy_started_session(service: PurchaseService, session: Session) -> None:
    with pytest.raises(PurchaseNotAllowedError):
        await service.purchase(
            customer_id="cus_1", session_id=session.id, quantity=1, now=session.starts_at
        )


async def test_unknown_customer_raises(service: PurchaseService, session: Session) -> None:
    with pytest.raises(NotFoundError):
        await service.purchase(
            customer_id="cus_x",
            session_id=session.id,
            quantity=1,
            now=session.starts_at - timedelta(days=1),
        )

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from headache_core.domain import (
    Customer,
    Event,
    EventCategory,
    Money,
    Order,
    OrderStatus,
    Session,
)

SESSION_START = datetime(2026, 11, 20, 20, 0, tzinfo=UTC)


@pytest.fixture
def customer() -> Customer:
    return Customer("cus_1", "Ana Pérez", "ana@example.com", "ES", "es")


@pytest.fixture
def event() -> Event:
    return Event(
        id="evt_1",
        title="Candlelit Strings: Vivaldi's Four Seasons",
        category=EventCategory.CANDLELIT,
        city="Madrid",
        venue_id="ven_1",
        description="Concierto a la luz de las velas.",
        price=Money(Decimal("30.00"), "EUR"),
        refundable=True,
        refund_window_hours=48,
    )


@pytest.fixture
def session() -> Session:
    return Session(
        id="ses_1", event_id="evt_1", starts_at=SESSION_START, capacity=200, available=50
    )


@pytest.fixture
def order() -> Order:
    return Order(
        id="ord_1",
        customer_id="cus_1",
        session_id="ses_1",
        quantity=2,
        total=Money(Decimal("60.00"), "EUR"),
        status=OrderStatus.PAID,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )

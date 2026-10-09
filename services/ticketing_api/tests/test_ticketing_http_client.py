"""Contrato consumidor-proveedor: HttpTicketingClient contra la app FastAPI real (sin red)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from headache_core.adapters.memory import InMemoryUnitOfWork
from headache_core.adapters.ticketing_http import HttpTicketingClient
from headache_core.domain import (
    Customer,
    Event,
    EventCategory,
    Money,
    Order,
    OrderStatus,
    RefundStatus,
    Session,
)
from headache_core.ports import (
    TicketingNotFoundError,
    TicketingUnavailableError,
)
from ticketing_api.app import create_app

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PRICE = Money(Decimal("30.00"), "EUR")


@pytest.fixture
async def client() -> AsyncIterator[HttpTicketingClient]:
    uow = InMemoryUnitOfWork(
        customers=[Customer("cus_1", "Ana Pérez", "ana@example.com", "ES", "es")],
        events=[
            Event(
                "evt_1",
                "Candlelit Strings",
                EventCategory.CANDLELIT,
                "Madrid",
                "ven_1",
                "Concierto.",
                PRICE,
                refundable=True,
                refund_window_hours=48,
            )
        ],
        sessions=[Session("ses_1", "evt_1", datetime(2026, 11, 20, 20, tzinfo=UTC), 100, 3)],
        orders=[Order("ord_1", "cus_1", "ses_1", 2, PRICE * 2, OrderStatus.PAID, NOW)],
    )
    app = create_app(uow_factory=lambda: uow, clock=lambda: NOW)
    transport = httpx.ASGITransport(app=app)
    http = httpx.AsyncClient(transport=transport, base_url="http://t")
    async with HttpTicketingClient(http) as c:
        yield c


async def test_reads_map_to_domain_models(client: HttpTicketingClient) -> None:
    [event] = await client.search_events(city="Madrid", category=EventCategory.CANDLELIT)
    sessions = await client.list_sessions("evt_1")
    order = await client.get_order("ord_1")
    orders = await client.list_customer_orders("cus_1", limit=5)

    assert event.price == PRICE
    assert await client.get_event("evt_1") == event
    assert [s.id for s in sessions] == ["ses_1"]
    assert sessions[0].starts_at == datetime(2026, 11, 20, 20, tzinfo=UTC)
    assert order.total == PRICE * 2
    assert orders == [order]


async def test_refund_round_trip(client: HttpTicketingClient) -> None:
    result = await client.request_refund("ord_1", reason="No puedo ir")

    assert result.approved is True
    assert result.status is RefundStatus.APPROVED
    assert result.amount == PRICE * 2
    assert (await client.get_order("ord_1")).status is OrderStatus.REFUNDED


async def test_not_found_is_typed(client: HttpTicketingClient) -> None:
    with pytest.raises(TicketingNotFoundError, match="ord_9"):
        await client.get_order("ord_9")


async def test_transport_failures_are_unavailable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(boom), base_url="http://t")
    async with HttpTicketingClient(http) as c:
        with pytest.raises(TicketingUnavailableError):
            await c.get_event("evt_1")

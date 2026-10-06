from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from headache_core.adapters.memory import InMemoryUnitOfWork
from headache_core.domain import Customer, Event, EventCategory, Money, Session
from ticketing_api.app import create_app

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    event = Event(
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
    return InMemoryUnitOfWork(
        customers=[Customer("cus_1", "Ana Pérez", "ana@example.com", "ES", "es")],
        events=[event],
        sessions=[
            Session("ses_1", "evt_1", datetime(2026, 11, 20, 20, tzinfo=UTC), 100, 3),
            Session("ses_2", "evt_1", datetime(2026, 9, 1, 20, tzinfo=UTC), 100, 50),
        ],
    )


@pytest.fixture
async def client(uow: InMemoryUnitOfWork) -> AsyncIterator[AsyncClient]:
    app = create_app(uow_factory=lambda: uow, clock=lambda: NOW)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_lists_events_by_city(client: AsyncClient) -> None:
    response = await client.get("/events", params={"city": "Madrid", "category": "candlelit"})

    assert response.status_code == 200
    assert [e["id"] for e in response.json()] == ["evt_1"]
    assert response.json()[0]["price"] == {"amount": "30.00", "currency": "EUR"}


async def test_availability_only_shows_upcoming_sessions(client: AsyncClient) -> None:
    response = await client.get("/events/evt_1/sessions")

    assert [s["id"] for s in response.json()] == ["ses_1"]


async def test_purchase_then_read_order(client: AsyncClient) -> None:
    created = await client.post(
        "/orders",
        json={"customer_id": "cus_1", "session_id": "ses_1", "quantity": 2},
        headers={"Idempotency-Key": "abc"},
    )
    assert created.status_code == 201
    order = created.json()
    assert order["total"] == {"amount": "60.00", "currency": "EUR"}

    fetched = await client.get(f"/orders/{order['id']}")
    retried = await client.post(
        "/orders",
        json={"customer_id": "cus_1", "session_id": "ses_1", "quantity": 2},
        headers={"Idempotency-Key": "abc"},
    )
    session = await client.get("/sessions/ses_1")

    assert fetched.json() == order
    assert retried.json()["id"] == order["id"]
    assert session.json()["available"] == 1


async def test_sold_out_returns_409(client: AsyncClient) -> None:
    response = await client.post(
        "/orders", json={"customer_id": "cus_1", "session_id": "ses_1", "quantity": 4}
    )

    assert response.status_code == 409
    assert "plazas" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"customer_id": "cus_1", "session_id": "ses_1", "quantity": 11},
        {"customer_id": "cus_1", "session_id": "ses_1; DROP TABLE orders", "quantity": 1},
        {"customer_id": "cus_1", "session_id": "ses_1", "quantity": 1, "price": 0},
    ],
)
async def test_invalid_input_returns_422(client: AsyncClient, body: dict[str, object]) -> None:
    response = await client.post("/orders", json=body)

    assert response.status_code == 422


async def test_unknown_order_returns_404(client: AsyncClient) -> None:
    response = await client.get("/orders/ord_missing")

    assert response.status_code == 404


async def test_refund_flow(client: AsyncClient) -> None:
    order = (
        await client.post(
            "/orders", json={"customer_id": "cus_1", "session_id": "ses_1", "quantity": 1}
        )
    ).json()

    response = await client.post(f"/orders/{order['id']}/refunds", json={"reason": "Enfermedad"})

    assert response.status_code == 201
    refund = response.json()
    assert refund["approved"] is True
    assert refund["status"] == "approved"
    assert (await client.get(f"/orders/{order['id']}")).json()["status"] == "refunded"

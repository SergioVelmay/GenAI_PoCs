from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from headache_core.adapters.memory import FakeLLM, InMemoryUnitOfWork
from headache_core.adapters.ticketing_local import UnitOfWorkTicketing
from headache_core.application.tools import (
    TICKETING_AGENT_PROMPT,
    ToolContext,
    ToolLoop,
    build_ticketing_tools,
)
from headache_core.domain import (
    Customer,
    Event,
    EventCategory,
    Money,
    Order,
    OrderStatus,
    Session,
)
from headache_core.ports import ChatMessage, Role, ToolCall

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
ANA = ToolContext(customer_id="cus_1")


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    price = Money(Decimal("30.00"), "EUR")
    return InMemoryUnitOfWork(
        customers=[
            Customer("cus_1", "Ana Pérez", "ana@example.com", "ES", "es"),
            Customer("cus_2", "Bob Smith", "bob@example.com", "GB", "en"),
        ],
        events=[
            Event(
                "evt_1",
                "Candlelit Strings: Vivaldi",
                EventCategory.CANDLELIT,
                "Madrid",
                "ven_1",
                "Concierto a la luz de las velas.\u200b",
                price,
                refundable=True,
                refund_window_hours=48,
            )
        ],
        sessions=[Session("ses_1", "evt_1", datetime(2026, 11, 20, 20, tzinfo=UTC), 100, 3)],
        orders=[
            Order("ord_1", "cus_1", "ses_1", 2, price * 2, OrderStatus.PAID, NOW),
            Order("ord_2", "cus_2", "ses_1", 1, price, OrderStatus.PAID, NOW),
        ],
    )


def _call(call_id: str, name: str, **arguments: Any) -> ToolCall:
    return ToolCall(call_id, name, json.dumps(arguments))


async def _run(uow: InMemoryUnitOfWork, *calls: ToolCall) -> list[Any]:
    registry = build_ticketing_tools(UnitOfWorkTicketing(lambda: uow, clock=lambda: NOW))
    executions = await ToolLoop(FakeLLM(), registry).execute_turn(calls, ANA)
    return [json.loads(e.output) for e in executions]


async def test_catalog_tools_return_projections(uow: InMemoryUnitOfWork) -> None:
    events, detail, sessions = await _run(
        uow,
        _call("a", "search_events", city="Madrid", category="candlelit", limit=5),
        _call("b", "get_event", event_id="evt_1"),
        _call("c", "list_sessions", event_id="evt_1", only_available=True),
    )

    assert events["items"][0] == {
        "id": "evt_1",
        "title": "Candlelit Strings: Vivaldi",
        "category": "candlelit",
        "city": "Madrid",
        "price": "30.00 EUR",
        "refundable": True,
        "refund_window_hours": 48,
    }
    assert detail["description"] == "Concierto a la luz de las velas."
    assert "venue_id" not in detail
    assert sessions["items"] == [
        {"id": "ses_1", "starts_at": "2026-11-20T20:00:00Z", "available": 3}
    ]


async def test_orders_are_scoped_to_the_authenticated_customer(uow: InMemoryUnitOfWork) -> None:
    mine, own, foreign = await _run(
        uow,
        _call("a", "list_my_orders", limit=5),
        _call("b", "get_order", order_id="ord_1"),
        _call("c", "get_order", order_id="ord_2"),
    )

    assert [o["id"] for o in mine["items"]] == ["ord_1"]
    assert "customer_id" not in own
    assert foreign["error"]["type"] == "not_found"
    assert foreign["error"]["message"] == "El cliente no tiene ningún pedido ord_2."


async def test_refund_is_decided_by_domain_policy(uow: InMemoryUnitOfWork) -> None:
    [refund] = await _run(uow, _call("a", "request_refund", order_id="ord_1", reason="No puedo ir"))
    [again] = await _run(uow, _call("b", "request_refund", order_id="ord_1", reason="Otra vez"))

    assert refund["approved"] is True
    assert refund["amount"] == "60.00 EUR"
    assert again["approved"] is False
    assert "refunded" in again["decision_reason"]


async def test_cannot_refund_another_customers_order(uow: InMemoryUnitOfWork) -> None:
    [result] = await _run(uow, _call("a", "request_refund", order_id="ord_2", reason="x"))

    assert result["error"]["type"] == "not_found"
    order = await uow.orders.get("ord_2")
    assert order is not None and order.status is OrderStatus.PAID


async def test_invalid_ids_are_rejected_before_calling_the_api(uow: InMemoryUnitOfWork) -> None:
    [result] = await _run(uow, _call("a", "get_order", order_id="../../admin"))

    assert result["error"]["type"] == "invalid_arguments"


def test_tool_catalog_exposes_strict_schemas() -> None:
    registry = build_ticketing_tools(UnitOfWorkTicketing(InMemoryUnitOfWork))
    config = registry.config()
    specs = {spec.name: spec for spec in config.tools}

    assert set(specs) == {
        "search_events",
        "get_event",
        "list_sessions",
        "list_my_orders",
        "get_order",
        "request_refund",
    }
    refund = specs["request_refund"].json_schema
    assert refund["required"] == ["order_id", "reason"]
    # Prefijo exacto por tipo de ID (regresión: nano pasó un ses_... a get_event).
    assert refund["properties"]["order_id"]["pattern"].startswith("^ord_")
    assert specs["get_event"].json_schema["properties"]["event_id"]["pattern"].startswith("^evt_")
    assert "maxLength" not in refund["properties"]["reason"]
    # Ninguna tool deja elegir al modelo a qué cliente suplantar.
    assert all("customer_id" not in s.json_schema["properties"] for s in specs.values())


async def test_end_to_end_loop_with_scripted_model(uow: InMemoryUnitOfWork) -> None:
    llm = FakeLLM(
        [
            [_call("c1", "list_my_orders", limit=5)],
            [_call("c2", "request_refund", order_id="ord_1", reason="Enfermedad")],
            "Tu reembolso de 60.00 EUR está aprobado.",
        ]
    )
    loop = ToolLoop(llm, build_ticketing_tools(UnitOfWorkTicketing(lambda: uow, lambda: NOW)))

    result = await loop.run(
        [
            ChatMessage(Role.SYSTEM, TICKETING_AGENT_PROMPT, cache_checkpoint=True),
            ChatMessage(Role.USER, "Quiero devolver mi última compra, estoy enfermo."),
        ],
        ANA,
    )

    assert result.stop_reason == "answer"
    assert [e.call.name for e in result.executions] == ["list_my_orders", "request_refund"]
    order = await uow.orders.get("ord_1")
    assert order is not None and order.status is OrderStatus.REFUNDED

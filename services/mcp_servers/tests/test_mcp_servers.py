from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.utilities.tests import asgi_server

from headache_core.adapters.memory import InMemoryUnitOfWork
from headache_core.adapters.ticketing_local import UnitOfWorkTicketing
from headache_core.domain import (
    Customer,
    Event,
    EventCategory,
    Money,
    Order,
    OrderStatus,
    Session,
)
from headache_mcp.adapter import fixed_identity
from headache_mcp.contracts import CONTRACTS_DIR, contract_servers, describe
from headache_mcp.servers import (
    create_catalog_server,
    create_refunds_server,
    create_ticketing_server,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PRICE = Money(Decimal("30.00"), "EUR")


@pytest.fixture
def ticketing() -> UnitOfWorkTicketing:
    uow = InMemoryUnitOfWork(
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
                "Concierto a la luz de las velas.",
                PRICE,
                refundable=True,
                refund_window_hours=48,
            )
        ],
        sessions=[Session("ses_1", "evt_1", datetime(2026, 11, 20, 20, tzinfo=UTC), 100, 3)],
        orders=[
            Order("ord_1", "cus_1", "ses_1", 2, PRICE * 2, OrderStatus.PAID, NOW),
            Order("ord_2", "cus_2", "ses_1", 1, PRICE, OrderStatus.PAID, NOW),
        ],
    )
    return UnitOfWorkTicketing(lambda: uow, clock=lambda: NOW)


async def _call(server: FastMCP[Any], tool: str, **arguments: Any) -> tuple[bool, dict[str, Any]]:
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    text = result.content[0].text
    return result.is_error, json.loads(text)


@pytest.mark.parametrize("name", ["catalog", "ticketing", "refunds"])
async def test_tool_contracts_match_versioned_snapshot(name: str) -> None:
    expected = json.loads((CONTRACTS_DIR / f"{name}.json").read_text(encoding="utf-8"))

    assert await describe(contract_servers()[name]) == expected


async def test_each_server_exposes_only_its_bounded_context() -> None:
    tools = {
        name: {t["name"] for t in (await describe(server))["tools"]}
        for name, server in contract_servers().items()
    }

    assert tools == {
        "catalog": {"search_events", "get_event", "list_sessions"},
        "ticketing": {"list_my_orders", "get_order"},
        "refunds": {"request_refund"},
    }


async def test_annotations_flag_side_effects() -> None:
    refunds = await describe(contract_servers()["refunds"])
    catalog = await describe(contract_servers()["catalog"])

    assert refunds["tools"][0]["annotations"]["destructiveHint"] is True
    assert all(t["annotations"]["readOnlyHint"] for t in catalog["tools"])


async def test_catalog_search_returns_structured_projection(ticketing: UnitOfWorkTicketing) -> None:
    async with Client(create_catalog_server(ticketing)) as client:
        result = await client.call_tool("search_events", {"city": "Madrid", "limit": 3})

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["items"][0]["id"] == "evt_1"
    assert "venue_id" not in result.structured_content["items"][0]


async def test_ticketing_scopes_orders_to_identity(ticketing: UnitOfWorkTicketing) -> None:
    server = create_ticketing_server(ticketing, identity=fixed_identity("cus_1"))

    own_error, own = await _call(server, "get_order", order_id="ord_1")
    foreign_error, foreign = await _call(server, "get_order", order_id="ord_2")

    assert not own_error and own["id"] == "ord_1"
    assert foreign_error and foreign["error"]["type"] == "not_found"


async def test_unauthenticated_calls_are_rejected(ticketing: UnitOfWorkTicketing) -> None:
    server = create_ticketing_server(ticketing, identity=fixed_identity(""))

    is_error, payload = await _call(server, "list_my_orders", limit=5)

    assert is_error and payload["error"]["type"] == "unauthenticated"


async def test_invalid_arguments_are_typed_errors(ticketing: UnitOfWorkTicketing) -> None:
    server = create_refunds_server(ticketing, identity=fixed_identity("cus_1"))

    is_error, payload = await _call(server, "request_refund", order_id="DROP TABLE", reason="x")

    assert is_error and payload["error"]["type"] == "invalid_arguments"


async def test_refund_over_streamable_http(ticketing: UnitOfWorkTicketing) -> None:
    server = create_refunds_server(ticketing, identity=fixed_identity("cus_1"))

    async with asgi_server(server) as running:
        async with running.http_client() as http:
            health = await http.get("/health")
        async with running.client() as client:
            result = await client.call_tool(
                "request_refund", {"order_id": "ord_1", "reason": "No puedo ir"}
            )

    assert health.json() == {"status": "ok", "server": "headache-refunds"}
    assert result.structured_content is not None
    assert result.structured_content["approved"] is True
    assert result.structured_content["amount"] == "60.00 EUR"

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.utilities.tests import asgi_server

from headache_core.adapters.memory import FakeLLM, InMemoryUnitOfWork
from headache_core.adapters.ticketing_local import UnitOfWorkTicketing
from headache_core.application.tools import Tool, ToolContext, ToolLoop, ToolRegistry
from headache_core.config import McpSettings
from headache_core.domain import (
    Customer,
    Event,
    EventCategory,
    Money,
    Order,
    OrderStatus,
    Session,
)
from headache_core.ports import ToolCall
from headache_mcp.adapter import anonymous
from headache_mcp.auth import SERVER_SCOPES, DevTokenIssuer, jwt_auth, token_identity
from headache_mcp.servers import SERVERS, create_server
from support_agent.agent import SupportAgent
from support_agent.toolset import McpToolset, RemoteArgs

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PRICE = Money(Decimal("30.00"), "EUR")
ANA = ToolContext(customer_id="cus_1")


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
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


@pytest.fixture
async def clients(uow: InMemoryUnitOfWork, tmp_path: Path) -> AsyncIterator[dict[str, Client[Any]]]:
    """Los tres servidores con OAuth, por Streamable HTTP en proceso; un token por servidor."""
    issuer = DevTokenIssuer.load_or_create(tmp_path / "key.pem", issuer="https://auth.test")
    settings = McpSettings(
        auth="jwt", issuer="https://auth.test", public_key_file=str(tmp_path / "key.pub")
    )
    ticketing = UnitOfWorkTicketing(lambda: uow, clock=lambda: NOW)
    async with AsyncExitStack() as stack:
        result: dict[str, Client[Any]] = {}
        for name in SERVERS:
            server = create_server(
                name,
                ticketing,
                identity=anonymous if name == "catalog" else token_identity(),
                auth=jwt_auth(settings, name, public_url="http://127.0.0.1"),
            )
            running = await stack.enter_async_context(asgi_server(server))
            token = issuer.mint(
                subject="cus_1", audience=settings.audience(name), scopes=SERVER_SCOPES[name]
            )
            result[name] = running.client(auth=token)
        yield result


def _call(call_id: str, name: str, **arguments: Any) -> ToolCall:
    return ToolCall(call_id, name, json.dumps(arguments))


async def test_discovers_tools_from_all_servers(clients: dict[str, Client[Any]]) -> None:
    async with McpToolset(clients) as tools:
        specs = {spec.name: spec for spec in tools.config().tools}
        writes = [t.name for t in tools if not t.read_only]

    assert set(specs) == {
        "search_events",
        "get_event",
        "list_sessions",
        "list_my_orders",
        "get_order",
        "request_refund",
    }
    assert writes == ["request_refund"]
    # Los esquemas publicados por nuestros servidores son aptos para el modo strict.
    assert all(spec.strict for spec in specs.values())


async def test_agent_refunds_through_mcp_with_token_identity(
    clients: dict[str, Client[Any]], uow: InMemoryUnitOfWork
) -> None:
    llm = FakeLLM(
        [
            [_call("c1", "list_my_orders", limit=5), _call("c2", "search_events", city="Madrid")],
            [_call("c3", "request_refund", order_id="ord_1", reason="Estoy enfermo")],
            "Reembolso aprobado: 60.00 EUR.",
        ]
    )

    async with McpToolset(clients) as tools:
        result = await SupportAgent(llm, tools).answer("Devuélveme mi última compra", ANA)

    assert result.stop_reason == "answer"
    assert [e.error for e in result.executions] == [None, None, None]
    refund = json.loads(result.executions[-1].output)
    assert refund["approved"] is True
    order = await uow.orders.get("ord_1")
    assert order is not None and order.status is OrderStatus.REFUNDED


async def test_remote_errors_keep_their_type(clients: dict[str, Client[Any]]) -> None:
    async with McpToolset(clients) as tools:
        loop = ToolLoop(FakeLLM(), tools)
        foreign, invalid = await loop.execute_turn(
            [_call("a", "get_order", order_id="ord_2"), _call("b", "get_order", order_id="x")],
            ANA,
        )

    assert foreign.error == "not_found"
    assert invalid.error == "invalid_arguments"


async def test_transport_failures_become_unavailable(
    clients: dict[str, Client[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("conexión rechazada")

    async with McpToolset(clients) as tools:
        monkeypatch.setattr(clients["catalog"], "call_tool", broken)
        [execution] = await ToolLoop(FakeLLM(), tools).execute_turn(
            [_call("a", "search_events", city="Madrid")], ANA
        )

    error = json.loads(execution.output)["error"]
    assert error == {
        "type": "unavailable",
        "message": "El servicio 'catalog' no está disponible.",
        "retryable": True,
    }


def test_lax_third_party_schemas_are_not_sent_as_strict() -> None:
    async def noop(args: RemoteArgs, _: ToolContext) -> None:
        return None

    tool = Tool(
        "x",
        "x",
        RemoteArgs,
        noop,
        published_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )

    assert ToolRegistry([tool]).config().tools[0].strict is False

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.utilities.tests import ASGIServer, asgi_server

from headache_core.adapters.memory import InMemoryUnitOfWork
from headache_core.adapters.ticketing_local import UnitOfWorkTicketing
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
from headache_mcp.adapter import anonymous, fixed_identity
from headache_mcp.auth import (
    GATEWAY_SCOPES,
    SERVER_SCOPES,
    DevTokenIssuer,
    jwt_auth,
    token_identity,
)
from headache_mcp.contracts import CONTRACTS_DIR, contract_servers, describe
from headache_mcp.gateway import DevTokenExchanger, TokenExchanger, create_gateway
from headache_mcp.servers import create_server

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
    async with Client(create_server("catalog", ticketing)) as client:
        result = await client.call_tool("search_events", {"city": "Madrid", "limit": 3})

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["items"][0]["id"] == "evt_1"
    assert "venue_id" not in result.structured_content["items"][0]


async def test_ticketing_scopes_orders_to_identity(ticketing: UnitOfWorkTicketing) -> None:
    server = create_server("ticketing", ticketing, identity=fixed_identity("cus_1"))

    own_error, own = await _call(server, "get_order", order_id="ord_1")
    foreign_error, foreign = await _call(server, "get_order", order_id="ord_2")

    assert not own_error and own["id"] == "ord_1"
    assert foreign_error and foreign["error"]["type"] == "not_found"


async def test_unauthenticated_calls_are_rejected(ticketing: UnitOfWorkTicketing) -> None:
    server = create_server("ticketing", ticketing, identity=fixed_identity(""))

    is_error, payload = await _call(server, "list_my_orders", limit=5)

    assert is_error and payload["error"]["type"] == "unauthenticated"


async def test_invalid_arguments_are_typed_errors(ticketing: UnitOfWorkTicketing) -> None:
    server = create_server("refunds", ticketing, identity=fixed_identity("cus_1"))

    is_error, payload = await _call(server, "request_refund", order_id="DROP TABLE", reason="x")

    assert is_error and payload["error"]["type"] == "invalid_arguments"


async def test_refund_over_streamable_http(ticketing: UnitOfWorkTicketing) -> None:
    server = create_server("refunds", ticketing, identity=fixed_identity("cus_1"))

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


# ---------- OAuth 2.1: los servidores como resource servers ----------

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
def issuer(tmp_path: Path) -> DevTokenIssuer:
    return DevTokenIssuer.load_or_create(tmp_path / "key.pem", issuer="https://auth.test")


def _secured(name: str, ticketing: UnitOfWorkTicketing, tmp_path: Path) -> FastMCP[Any]:
    settings = McpSettings(
        auth="jwt", issuer="https://auth.test", public_key_file=str(tmp_path / "key.pub")
    )
    return create_server(
        name,
        ticketing,
        identity=anonymous if name == "catalog" else token_identity(),
        auth=jwt_auth(settings, name, public_url="http://127.0.0.1"),
    )


def _token(issuer: DevTokenIssuer, server: str, *, subject: str = "cus_1", **kwargs: Any) -> str:
    options: dict[str, Any] = {"scopes": SERVER_SCOPES[server], **kwargs}
    return issuer.mint(subject=subject, audience=f"api://headache-mcp/{server}", **options)


async def test_identity_comes_from_the_token(
    ticketing: UnitOfWorkTicketing, issuer: DevTokenIssuer, tmp_path: Path
) -> None:
    server = _secured("ticketing", ticketing, tmp_path)

    async with (
        asgi_server(server) as running,
        running.client(auth=_token(issuer, "ticketing")) as client,
    ):
        mine = await client.call_tool("list_my_orders", {"limit": 5})
        foreign = await client.call_tool("get_order", {"order_id": "ord_2"}, raise_on_error=False)

    assert mine.structured_content is not None
    assert [o["id"] for o in mine.structured_content["items"]] == ["ord_1"]
    assert foreign.is_error


@pytest.mark.parametrize(
    ("case", "status"),
    [
        ("missing", 401),
        ("wrong_audience", 401),
        ("expired", 401),
        ("wrong_issuer", 401),
        # El verificador trata un scope de servidor ausente como token inválido (401, no 403).
        ("insufficient_scope", 401),
    ],
)
async def test_invalid_tokens_are_rejected_before_any_tool_runs(
    case: str, status: int, ticketing: UnitOfWorkTicketing, issuer: DevTokenIssuer, tmp_path: Path
) -> None:
    tokens = {
        "missing": None,
        # Un token emitido para `ticketing` no se puede reutilizar en `refunds` (passthrough).
        "wrong_audience": _token(issuer, "ticketing"),
        "expired": _token(issuer, "refunds", expires_in_seconds=-60),
        "wrong_issuer": DevTokenIssuer.load_or_create(
            tmp_path / "other" / "key.pem", issuer="https://evil.test"
        ).mint(subject="cus_1", audience="api://headache-mcp/refunds", scopes=["refunds:write"]),
        "insufficient_scope": _token(issuer, "refunds", scopes=["orders:read"]),
    }
    token = tokens[case]
    headers = MCP_HEADERS if token is None else {**MCP_HEADERS, "Authorization": f"Bearer {token}"}

    async with (
        asgi_server(_secured("refunds", ticketing, tmp_path)) as running,
        running.http_client() as http,
    ):
        response = await http.post("/mcp", json=INIT, headers=headers)

    assert response.status_code == status
    assert "resource_metadata" in response.headers["www-authenticate"]


async def test_protected_resource_metadata_advertises_issuer_and_scopes(
    ticketing: UnitOfWorkTicketing, tmp_path: Path, issuer: DevTokenIssuer
) -> None:
    async with (
        asgi_server(_secured("refunds", ticketing, tmp_path)) as running,
        running.http_client() as http,
    ):
        response = await http.get("/.well-known/oauth-protected-resource/mcp")

    metadata = response.json()
    assert metadata["resource"] == "http://127.0.0.1/mcp"
    assert metadata["authorization_servers"] == ["https://auth.test/"]
    assert metadata["scopes_supported"] == ["refunds:write"]


def test_dev_issuer_reuses_its_key(tmp_path: Path) -> None:
    first = DevTokenIssuer.load_or_create(tmp_path / "k.pem", issuer="https://auth.test")
    second = DevTokenIssuer.load_or_create(tmp_path / "k.pem", issuer="https://auth.test")

    assert first.public_key == second.public_key


# ---------- MCP Gateway propio (carril B) ----------


class SpyExchanger(DevTokenExchanger):
    def __init__(self, issuer: DevTokenIssuer) -> None:
        super().__init__(issuer)
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    async def exchange(self, *, subject: str, audience: str, scopes: Sequence[str]) -> str:
        self.calls.append((subject, audience, tuple(scopes)))
        return await super().exchange(subject=subject, audience=audience, scopes=scopes)


@asynccontextmanager
async def _gateway(
    ticketing: UnitOfWorkTicketing,
    tmp_path: Path,
    exchanger: TokenExchanger,
    *,
    requests_per_second: float = 50.0,
) -> AsyncIterator[ASGIServer]:
    settings = McpSettings(
        auth="jwt", issuer="https://auth.test", public_key_file=str(tmp_path / "key.pub")
    )
    async with AsyncExitStack() as stack:
        backends = {
            name: await stack.enter_async_context(asgi_server(_secured(name, ticketing, tmp_path)))
            for name in SERVER_SCOPES
        }
        gateway = create_gateway(
            list(backends),
            settings=settings,
            connect=lambda name, token: backends[name].client(auth=token),
            exchanger=exchanger,
            auth=jwt_auth(
                settings, "gateway", public_url="http://127.0.0.1", scopes=GATEWAY_SCOPES
            ),
            requests_per_second=requests_per_second,
        )
        yield await stack.enter_async_context(asgi_server(gateway))


def _gateway_token(issuer: DevTokenIssuer, scopes: list[str] = GATEWAY_SCOPES) -> str:
    return issuer.mint(subject="cus_1", audience="api://headache-mcp/gateway", scopes=scopes)


async def test_gateway_aggregates_servers_and_exchanges_tokens(
    ticketing: UnitOfWorkTicketing, issuer: DevTokenIssuer, tmp_path: Path
) -> None:
    exchanger = SpyExchanger(issuer)

    async with (
        _gateway(ticketing, tmp_path, exchanger) as running,
        running.client(auth=_gateway_token(issuer)) as client,
    ):
        tools = {t.name for t in await client.list_tools()}
        mine = await client.call_tool("list_my_orders", {"limit": 5})
        foreign = await client.call_tool("get_order", {"order_id": "ord_2"}, raise_on_error=False)

    assert tools == {
        "search_events",
        "get_event",
        "list_sessions",
        "list_my_orders",
        "get_order",
        "request_refund",
    }
    assert mine.structured_content is not None
    assert [o["id"] for o in mine.structured_content["items"]] == ["ord_1"]
    assert foreign.is_error
    # Cada servidor recibe un token propio: su audience y solo su scope, con el mismo usuario.
    assert ("cus_1", "api://headache-mcp/ticketing", ("orders:read",)) in exchanger.calls
    assert {audience for _, audience, _ in exchanger.calls} <= {
        f"api://headache-mcp/{name}" for name in SERVER_SCOPES
    }


async def test_gateway_rejects_tokens_without_all_its_scopes(
    ticketing: UnitOfWorkTicketing, issuer: DevTokenIssuer, tmp_path: Path
) -> None:
    token = _gateway_token(issuer, scopes=["catalog:read"])

    async with (
        _gateway(ticketing, tmp_path, DevTokenExchanger(issuer)) as running,
        running.http_client() as http,
    ):
        response = await http.post(
            "/mcp", json=INIT, headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 401


async def test_gateway_rate_limits_each_caller(
    ticketing: UnitOfWorkTicketing, issuer: DevTokenIssuer, tmp_path: Path
) -> None:
    async with (
        _gateway(ticketing, tmp_path, DevTokenExchanger(issuer), requests_per_second=1) as running,
        running.client(auth=_gateway_token(issuer)) as client,
    ):
        with pytest.raises(Exception, match="Rate limit"):
            for _ in range(5):
                await client.call_tool("search_events", {"city": "Madrid"})

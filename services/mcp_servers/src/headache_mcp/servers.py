"""Los tres servidores MCP de Headache: un contexto acotado (y un radio de impacto) por servidor.

| Servidor  | Tools                                  | Datos personales | Escribe |
|-----------|----------------------------------------|------------------|---------|
| catalog   | search_events, get_event, list_sessions | no               | no      |
| ticketing | list_my_orders, get_order               | sí               | no      |
| refunds   | request_refund                          | sí               | sí      |

Separarlos permite desplegarlos, escalarlos y autorizarlos (scopes OAuth) por separado.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP

from headache_core.application.tools import (
    CATALOG_TOOLS,
    ORDER_TOOLS,
    REFUND_TOOLS,
    build_ticketing_tools,
)
from headache_core.ports import TicketingPort
from headache_mcp.adapter import IdentityResolver, Lifespan, anonymous, build_server

CATALOG_INSTRUCTIONS = (
    "Catálogo público de experiencias y eventos de Headache: búsqueda, detalle (incluida la "
    "política de reembolso) y sesiones con plazas libres. Fechas en UTC."
)
TICKETING_INSTRUCTIONS = (
    "Pedidos del cliente autenticado en Headache. Solo devuelve pedidos de ese cliente."
)
REFUNDS_INSTRUCTIONS = (
    "Solicitudes de reembolso de pedidos del cliente autenticado. La política de Headache "
    "decide y devuelve el motivo. Úsalo solo si el cliente lo pide explícitamente."
)


def create_catalog_server(
    ticketing: TicketingPort, *, lifespan: Lifespan | None = None
) -> FastMCP[Any]:
    registry = build_ticketing_tools(ticketing).subset(CATALOG_TOOLS)
    return build_server(
        "headache-catalog", CATALOG_INSTRUCTIONS, registry, identity=anonymous, lifespan=lifespan
    )


def create_ticketing_server(
    ticketing: TicketingPort, *, identity: IdentityResolver, lifespan: Lifespan | None = None
) -> FastMCP[Any]:
    registry = build_ticketing_tools(ticketing).subset(ORDER_TOOLS)
    return build_server(
        "headache-ticketing",
        TICKETING_INSTRUCTIONS,
        registry,
        identity=identity,
        lifespan=lifespan,
    )


def create_refunds_server(
    ticketing: TicketingPort, *, identity: IdentityResolver, lifespan: Lifespan | None = None
) -> FastMCP[Any]:
    registry = build_ticketing_tools(ticketing).subset(REFUND_TOOLS)
    return build_server(
        "headache-refunds", REFUNDS_INSTRUCTIONS, registry, identity=identity, lifespan=lifespan
    )


type ServerFactory = Callable[[TicketingPort, IdentityResolver, Lifespan | None], FastMCP[Any]]

SERVERS: dict[str, ServerFactory] = {
    "catalog": lambda t, _, life: create_catalog_server(t, lifespan=life),
    "ticketing": lambda t, ident, life: create_ticketing_server(t, identity=ident, lifespan=life),
    "refunds": lambda t, ident, life: create_refunds_server(t, identity=ident, lifespan=life),
}

"""Los tres servidores MCP de Genaipocs: un contexto acotado (y un radio de impacto) por servidor.

| Servidor  | Tools                                  | Datos personales | Escribe |
|-----------|----------------------------------------|------------------|---------|
| catalog   | search_events, get_event, list_sessions | no               | no      |
| ticketing | list_my_orders, get_order               | sí               | no      |
| refunds   | request_refund                          | sí               | sí      |

Separarlos permite desplegarlos, escalarlos y autorizarlos (scopes OAuth) por separado.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider

from genaipocs_core.application.tools import (
    CATALOG_TOOLS,
    ORDER_TOOLS,
    REFUND_TOOLS,
    build_ticketing_tools,
)
from genaipocs_core.ports import TicketingPort
from genaipocs_mcp.adapter import IdentityResolver, Lifespan, anonymous, build_server

SERVERS: dict[str, tuple[tuple[str, ...], str]] = {
    "catalog": (
        CATALOG_TOOLS,
        "Catálogo público de experiencias y eventos de Genaipocs: búsqueda, detalle (incluida la "
        "política de reembolso) y sesiones con plazas libres. Fechas en UTC.",
    ),
    "ticketing": (
        ORDER_TOOLS,
        "Pedidos del cliente autenticado en Genaipocs. Solo devuelve pedidos de ese cliente.",
    ),
    "refunds": (
        REFUND_TOOLS,
        "Solicitudes de reembolso de pedidos del cliente autenticado. La política de Genaipocs "
        "decide y devuelve el motivo. Úsalo solo si el cliente lo pide explícitamente.",
    ),
}


def create_server(
    name: str,
    ticketing: TicketingPort,
    *,
    identity: IdentityResolver = anonymous,
    auth: AuthProvider | None = None,
    lifespan: Lifespan | None = None,
) -> FastMCP[Any]:
    tools, instructions = SERVERS[name]
    return build_server(
        f"genaipocs-{name}",
        instructions,
        build_ticketing_tools(ticketing).subset(tools),
        identity=identity,
        auth=auth,
        lifespan=lifespan,
    )

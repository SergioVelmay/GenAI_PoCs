"""Arranca un servidor MCP con transporte Streamable HTTP (endpoint `/mcp`).

    uv run python -m headache_mcp catalog                 # http://127.0.0.1:8101/mcp
    $env:MCP__DEV_CUSTOMER_ID="cus_0000001"; uv run python -m headache_mcp ticketing
    uv run python -m headache_mcp refunds --port 8103

Requiere la API de ticketing (TICKETING_API__BASE_URL). Hasta la fase 6.3 (OAuth), los
servidores con datos personales actúan como MCP__DEV_CUSTOMER_ID y solo arrancan en local.
"""

from __future__ import annotations

import argparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP

from headache_core.adapters.ticketing_http import HttpTicketingClient
from headache_core.config import get_settings
from headache_mcp.adapter import anonymous, fixed_identity
from headache_mcp.servers import SERVERS

DEFAULT_PORTS = {"catalog": 8101, "ticketing": 8102, "refunds": 8103}


def main() -> None:
    parser = argparse.ArgumentParser(description="Servidores MCP de Headache")
    parser.add_argument("server", choices=sorted(SERVERS))
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    ticketing = HttpTicketingClient.from_settings(settings.ticketing_api)

    @asynccontextmanager
    async def lifespan(_: FastMCP[Any]) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await ticketing.aclose()

    identity = anonymous
    if args.server != "catalog":
        if settings.environment != "local":
            raise SystemExit(
                "Sin OAuth (fase 6.3), los servidores con datos personales son solo locales."
            )
        identity = fixed_identity(settings.mcp.dev_customer_id)

    server = SERVERS[args.server](ticketing, identity, lifespan)
    server.run(
        transport="http",
        host=args.host or settings.mcp.host,
        port=args.port or DEFAULT_PORTS[args.server],
        show_banner=False,
    )


if __name__ == "__main__":
    main()

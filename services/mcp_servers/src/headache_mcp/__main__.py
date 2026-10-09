"""Arranca un servidor MCP con transporte Streamable HTTP (endpoint `/mcp`).

    uv run python -m headache_mcp catalog                 # http://127.0.0.1:8101/mcp
    uv run python -m headache_mcp refunds --port 8103

Modos (MCP__AUTH):
- "jwt": resource server OAuth 2.1. En local, con el emisor de desarrollo:
      $env:MCP__AUTH="jwt"; $env:MCP__PUBLIC_KEY_FILE=".secrets/mcp-dev-key.pub"
  En la nube, MCP__JWKS_URI + MCP__ISSUER de Entra ID o Cognito.
- "none": identidad fija MCP__DEV_CUSTOMER_ID. Solo se admite con ENVIRONMENT=local.
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
from headache_mcp.auth import jwt_auth, token_identity
from headache_mcp.servers import SERVERS, create_server

DEFAULT_PORTS = {"catalog": 8101, "ticketing": 8102, "refunds": 8103}


def main() -> None:
    parser = argparse.ArgumentParser(description="Servidores MCP de Headache")
    parser.add_argument("server", choices=sorted(SERVERS))
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    port = args.port or DEFAULT_PORTS[args.server]
    personal_data = args.server != "catalog"

    if settings.mcp.auth == "jwt":
        public_url = settings.mcp.public_url_template.format(port=port)
        auth = jwt_auth(settings.mcp, args.server, public_url=public_url)
        identity = token_identity(settings.mcp.customer_claim) if personal_data else anonymous
    elif settings.environment == "local":
        auth = None
        identity = fixed_identity(settings.mcp.dev_customer_id) if personal_data else anonymous
    else:
        raise SystemExit("Fuera de local los servidores MCP exigen MCP__AUTH=jwt.")

    ticketing = HttpTicketingClient.from_settings(settings.ticketing_api)

    @asynccontextmanager
    async def lifespan(_: FastMCP[Any]) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await ticketing.aclose()

    server = create_server(args.server, ticketing, identity=identity, auth=auth, lifespan=lifespan)
    server.run(
        transport="http",
        host=args.host or settings.mcp.host,
        port=port,
        show_banner=False,
    )


if __name__ == "__main__":
    main()

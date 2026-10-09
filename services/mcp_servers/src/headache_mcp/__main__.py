"""Arranca un servidor MCP con transporte Streamable HTTP (endpoint `/mcp`).

    uv run python -m headache_mcp catalog                 # http://127.0.0.1:8101/mcp
    uv run python -m headache_mcp refunds --port 8103
    uv run python -m headache_mcp gateway                 # 8100: los tres tras un endpoint

Modos (MCP__AUTH):
- "jwt": resource server OAuth 2.1. En local, con el emisor de desarrollo:
      $env:MCP__AUTH="jwt"; $env:MCP__PUBLIC_KEY_FILE=".secrets/mcp-dev-key.pub"
  En la nube, MCP__JWKS_URI + MCP__ISSUER de Entra ID o Cognito.
- "none": identidad fija MCP__DEV_CUSTOMER_ID. Solo se admite con ENVIRONMENT=local.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastmcp import Client, FastMCP

from headache_core.adapters.ticketing_http import HttpTicketingClient
from headache_core.config import Settings, get_settings
from headache_mcp.adapter import anonymous, fixed_identity
from headache_mcp.auth import GATEWAY_SCOPES, DevTokenIssuer, jwt_auth, token_identity
from headache_mcp.gateway import DevTokenExchanger, create_gateway
from headache_mcp.servers import create_server

DEFAULT_PORTS = {"gateway": 8100, "catalog": 8101, "ticketing": 8102, "refunds": 8103}


def _gateway(settings: Settings, public_url: str) -> FastMCP[Any]:
    if settings.mcp.auth != "jwt" or settings.environment != "local":
        raise SystemExit("El gateway local exige MCP__AUTH=jwt y ENVIRONMENT=local (dev STS).")
    issuer = DevTokenIssuer.load_or_create(
        Path(settings.mcp.dev_private_key_file), issuer=settings.mcp.issuer
    )
    return create_gateway(
        list(settings.mcp.servers),
        settings=settings.mcp,
        connect=lambda name, token: Client(settings.mcp.servers[name], auth=token),
        exchanger=DevTokenExchanger(issuer),
        auth=jwt_auth(settings.mcp, "gateway", public_url=public_url, scopes=GATEWAY_SCOPES),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Servidores MCP de Headache")
    parser.add_argument("server", choices=sorted(DEFAULT_PORTS))
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = get_settings()
    port = args.port or DEFAULT_PORTS[args.server]
    host = args.host or settings.mcp.host
    public_url = settings.mcp.public_url_template.format(port=port)
    if args.server == "gateway":
        _gateway(settings, public_url).run(
            transport="http", host=host, port=port, show_banner=False
        )
        return

    personal_data = args.server != "catalog"
    if settings.mcp.auth == "jwt":
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
    server.run(transport="http", host=host, port=port, show_banner=False)


if __name__ == "__main__":
    main()

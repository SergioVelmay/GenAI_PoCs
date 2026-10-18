"""MCP Gateway propio (carril B): un único endpoint MCP delante de los tres servidores.

Lo mismo que AgentCore Gateway o APIM, en pequeño:
- Agregación: el agente se conecta a una URL y ve las tools de todos los servidores.
- Autenticación centralizada: valida el token del usuario (audience del gateway).
- Token exchange (RFC 8693 / On-Behalf-Of): cada servidor recibe un token NUEVO, con su
  propio audience y solo su scope. Nunca se reenvía el token de entrada (sin passthrough).
- Rate limit por usuario y auditoría de cada llamada a tool.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import mcp_types as mt
from fastmcp import Client, FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from fastmcp.server.providers.proxy import ProxyProvider
from fastmcp.tools.base import ToolResult
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from headache_core.application.tools import ToolError
from headache_core.config import McpSettings
from headache_mcp.auth import SERVER_SCOPES, DevTokenIssuer

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Gateway MCP de Headache: catálogo, pedidos y reembolsos del cliente autenticado en un "
    "único endpoint."
)

# (servidor, token del servidor) -> cliente MCP hacia ese servidor.
type BackendConnector = Callable[[str, str], Client[Any]]


class TokenExchanger(Protocol):
    async def exchange(
        self, *, subject: str, assertion: str, audience: str, scopes: Sequence[str]
    ) -> str:
        """`assertion`: el token de entrada (lo que necesita un OBO real); `subject`: su cliente."""
        ...


class DevTokenExchanger:
    """Solo local: el emisor de desarrollo actúa también como STS. En la nube,
    `EntraOboExchanger` (headache_mcp.entra) o el token exchange de AgentCore Identity."""

    def __init__(self, issuer: DevTokenIssuer, *, ttl_s: int = 300) -> None:
        self._issuer = issuer
        self._ttl_s = ttl_s

    async def exchange(
        self, *, subject: str, assertion: str, audience: str, scopes: Sequence[str]
    ) -> str:
        return self._issuer.mint(
            subject=subject, audience=audience, scopes=list(scopes), expires_in_seconds=self._ttl_s
        )


def _subject(claim: str) -> str | None:
    token = get_access_token()
    value = token.claims.get(claim) if token is not None else None
    return value if isinstance(value, str) and value else None


class AuditMiddleware(Middleware):
    """Una línea por llamada a tool: quién, qué, cuánto tardó y si falló (base de la fase 11)."""

    def __init__(self, claim: str) -> None:
        self._claim = claim

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        started = time.perf_counter()
        outcome = "error"
        try:
            result = await call_next(context)
            outcome = "error" if result.is_error else "ok"
            return result
        finally:
            logger.info(
                "mcp_tool_call caller=%s tool=%s outcome=%s latency_ms=%.0f",
                _subject(self._claim) or "anonymous",
                context.message.name,
                outcome,
                (time.perf_counter() - started) * 1000,
            )


def create_gateway(
    servers: Sequence[str],
    *,
    settings: McpSettings,
    connect: BackendConnector,
    exchanger: TokenExchanger,
    auth: AuthProvider | None,
    requests_per_second: float = 5.0,
) -> FastMCP[Any]:
    claim = settings.customer_claim
    gateway: FastMCP[Any] = FastMCP(
        "headache-gateway",
        INSTRUCTIONS,
        version="0.1.0",
        auth=auth,
        mask_error_details=True,
        middleware=[
            RateLimitingMiddleware(
                max_requests_per_second=requests_per_second,
                get_client_id=lambda _: _subject(claim) or "anonymous",
            ),
            AuditMiddleware(claim),
        ],
    )

    def factory(server: str) -> Callable[[], Any]:
        async def client() -> Client[Any]:
            subject = _subject(claim)
            incoming = get_access_token()
            if subject is None or incoming is None:
                raise ToolError("Petición no autenticada.", kind="unauthenticated")
            token = await exchanger.exchange(
                subject=subject,
                assertion=incoming.token,
                audience=settings.audience(server),
                scopes=SERVER_SCOPES[server],
            )
            return connect(server, token)

        return client

    for server in servers:
        gateway.add_provider(ProxyProvider(factory(server)))

    @gateway.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok", "server": "headache-gateway"})

    return gateway

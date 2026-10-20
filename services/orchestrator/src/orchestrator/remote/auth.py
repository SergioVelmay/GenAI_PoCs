"""OAuth entre agentes: el especialista es un *resource server*, como los servidores MCP (fase 6).

- Token de servicio a servicio (client credentials): `sub` = quien llama, un audience por
  especialista y el scope `cases:handle`. Equivalentes: Entra ID con *app roles* / Managed
  Identity en Azure y Cognito (client credentials) o IAM SigV4 en AWS.
- Solo se aceptan llamantes de una lista: el traspaso trae el `customer_id` y únicamente el
  orquestador puede afirmarlo. Si el flujo naciera de un cliente autenticado, se pasaría un
  token OBO y se exigiría `sub == envelope.customer_id`.
- La Agent Card es pública (descubrimiento) y declara el esquema bearer; el resto, con token.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Collection
from pathlib import Path
from typing import Any

import httpx
from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.types import ASGIApp, Receive, Scope, Send

from headache_core.config import A2ASettings
from headache_mcp.auth import DevTokenIssuer

PUBLIC_PATHS = frozenset({"/.well-known/agent-card.json", "/health"})

type TokenSource = Callable[[], Awaitable[str]]


def build_verifier(settings: A2ASettings, specialist: str) -> JWTVerifier:
    if bool(settings.jwks_uri) == bool(settings.public_key_file):
        raise ValueError("Configura A2A__JWKS_URI (nube) o A2A__PUBLIC_KEY_FILE (local), no ambos.")
    public_key = (
        Path(settings.public_key_file).read_text(encoding="utf-8")
        if settings.public_key_file
        else None
    )
    return JWTVerifier(
        public_key=public_key,
        jwks_uri=settings.jwks_uri or None,
        issuer=settings.issuer,
        audience=settings.audience(specialist),
        algorithm="RS256",
        # El scope lo comprueba el middleware: token válido sin scope = 403, no 401 (RFC 6750).
    )


class BearerAuthMiddleware:
    """Middleware ASGI: valida el bearer token antes de llegar al protocolo A2A."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        verifier: JWTVerifier,
        scope: str,
        allowed_callers: Collection[str],
    ) -> None:
        self._app = app
        self._verifier = verifier
        self._scope = scope
        self._allowed = frozenset(allowed_callers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in PUBLIC_PATHS:
            await self._app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        token = authorization.removeprefix("Bearer ").strip()
        access = await self._verifier.verify_token(token) if token else None
        if access is None:
            await _deny(send, 401, "invalid_token")
            return
        if self._scope not in access.scopes or access.claims.get("sub") not in self._allowed:
            await _deny(send, 403, "insufficient_scope")
            return
        scope.setdefault("state", {})["caller"] = access.claims.get("sub")
        await self._app(scope, receive, send)


async def _deny(send: Send, status: int, error: str) -> None:
    body = json.dumps({"error": error}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", f'Bearer error="{error}"'.encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BearerAuth(httpx.Auth):
    """Añade el token del orquestador a cada petición A2A (se renueva al caducar)."""

    def __init__(self, source: TokenSource) -> None:
        self._source = source

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {await self._source()}"
        yield request


def dev_token_source(
    issuer: DevTokenIssuer, settings: A2ASettings, specialist: str, *, ttl_s: int = 900
) -> TokenSource:
    """Solo local: el orquestador se emite su propio token con el emisor de desarrollo."""
    cache: dict[str, Any] = {}

    async def token() -> str:
        if cache.get("expires", 0.0) - 60 < time.time():
            cache["token"] = issuer.mint(
                subject=settings.caller_id,
                audience=settings.audience(specialist),
                scopes=[settings.scope],
                expires_in_seconds=ttl_s,
            )
            cache["expires"] = time.time() + ttl_s
        return str(cache["token"])

    return token

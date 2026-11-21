"""OAuth entre agentes: el especialista es un *resource server*, como los servidores MCP (fase 6).

- Token de servicio a servicio (client credentials): `sub` = quien llama, un audience por
  especialista y el scope `cases:handle`. Equivalentes: Entra ID con *app roles* / Managed
  Identity en Azure y Cognito (client credentials) o IAM SigV4 en AWS.
- **GCP (9.G4, `A2A__IDENTITY=gcp`)**: ID token de Google de la cuenta de servicio del
  orquestador (servidor de metadatos en Cloud Run; *impersonation* en local, sin claves). Firmado
  por Google (JWKS público), `aud` = *custom audience* del especialista en Cloud Run. **No lleva
  `scope`**: el llamante es el claim `email` (verificado) y lo autorizan la lista de llamantes y,
  antes, `roles/run.invoker` en Cloud Run. Viaja en `Authorization`: con
  `X-Serverless-Authorization`, Cloud Run le quita la firma y el contenedor no podría verificarlo.
- Solo se aceptan llamantes de una lista: el traspaso trae el `customer_id` y únicamente el
  orquestador puede afirmarlo. Si el flujo naciera de un cliente autenticado, se pasaría un
  token OBO y se exigiría `sub == envelope.customer_id`.
- La Agent Card es pública (descubrimiento) y declara el esquema bearer; el resto, con token.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.types import ASGIApp, Receive, Scope, Send

from genaipocs_core.config import A2ASettings
from genaipocs_mcp.auth import DevTokenIssuer

PUBLIC_PATHS = frozenset({"/.well-known/agent-card.json", "/health"})
# Emisor y claves públicas de los ID tokens de Google (las dos formas del `iss` son válidas).
GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"]
GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"

type TokenSource = Callable[[], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class CallerPolicy:
    """Quién puede llamar al especialista, una vez el token es válido (firma, `iss`, `aud`)."""

    allowed: frozenset[str]
    # Claim con la identidad del llamante: `sub` (JWT de servicio) o `email` (ID token de Google).
    claim: str = "sub"
    # Scope exigido; None = el emisor no emite scopes (Google): autoriza la lista y el IAM.
    scope: str | None = "cases:handle"

    def caller(self, claims: dict[str, Any]) -> str | None:
        caller = claims.get(self.claim)
        # Un `email` sin verificar no identifica a nadie (Google siempre lo verifica para
        # cuentas de servicio).
        if self.claim == "email" and claims.get("email_verified") is not True:
            return None
        return caller if isinstance(caller, str) else None


def caller_policy(settings: A2ASettings) -> CallerPolicy:
    allowed = frozenset(settings.allowed_callers)
    if settings.identity == "gcp":
        if not allowed or not all("@" in caller for caller in allowed):
            raise ValueError(
                "Con A2A__IDENTITY=gcp, A2A__ALLOWED_CALLERS son emails de cuentas de servicio."
            )
        return CallerPolicy(allowed, claim="email", scope=None)
    return CallerPolicy(allowed, scope=settings.scope)


def build_verifier(settings: A2ASettings, specialist: str) -> JWTVerifier:
    if settings.identity == "gcp":
        return JWTVerifier(
            jwks_uri=GOOGLE_JWKS_URI,
            issuer=GOOGLE_ISSUERS,
            audience=settings.audience(specialist),
            algorithm="RS256",
        )
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

    def __init__(self, app: ASGIApp, *, verifier: JWTVerifier, policy: CallerPolicy) -> None:
        self._app = app
        self._verifier = verifier
        self._policy = policy

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
        policy = self._policy
        caller = policy.caller(access.claims)
        if (policy.scope and policy.scope not in access.scopes) or caller not in policy.allowed:
            await _deny(send, 403, "insufficient_scope")
            return
        scope.setdefault("state", {})["caller"] = caller
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


def google_id_token_source(audience: str, fetch: Callable[[str], str]) -> TokenSource:
    """ID token de Google para un audience, renovado un minuto antes de caducar (duran 1 h).

    `fetch` es síncrono (la librería `google-auth` usa `requests`): corre en un hilo.
    """
    cache: dict[str, Any] = {}

    async def token() -> str:
        if cache.get("expires", 0.0) - 60 < time.time():
            value = await asyncio.to_thread(fetch, audience)
            cache["token"] = value
            cache["expires"] = _expiry(value)
        return str(cache["token"])

    return token


def _expiry(token: str) -> float:
    """`exp` del JWT sin verificar (lo verifica quien lo recibe); sin él, no se cachea."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return 0.0

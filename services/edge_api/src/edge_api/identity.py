"""Quién llama, a partir de un token firmado (nunca de una cabecera que el cliente pueda poner).

- Sin token: anónimo, con la IP como clave de rate limit (plan más restrictivo).
- Con token válido: `sub` como clave y el plan de un claim (`plan`). En la nube, Cognito
  (grupos o atributos) o Entra ID (*app roles*); en local, el emisor de desarrollo de la fase 6.
- Con token inválido: 401. Un token roto no se degrada a anónimo: sería un error silencioso.

Detrás de un balanceador, la IP real llega en `X-Forwarded-For` y solo es fiable si la pone el
propio balanceador (uvicorn `--forwarded-allow-ips`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import Request
from fastmcp.server.auth.providers.jwt import JWTVerifier

from edge_api.settings import EdgeSettings
from headache_core.config import get_settings
from headache_core.domain.admission import Caller, ClientPlan
from headache_mcp.auth import DevTokenIssuer

type CallerResolver = Callable[[Request], Awaitable[Caller]]


class UnauthenticatedError(Exception):
    """Bearer token presente pero inválido (firma, emisor, audience o caducidad)."""


def anonymous(request: Request) -> Caller:
    host = request.client.host if request.client else "unknown"
    return Caller(f"ip:{host}", ClientPlan.ANONYMOUS)


def plan_from_claim(value: object) -> ClientPlan:
    """Un plan desconocido es `public`: nunca se asciende a nadie por error."""
    if value in (ClientPlan.INTERNAL, ClientPlan.PUBLIC):
        return ClientPlan(str(value))
    return ClientPlan.PUBLIC


def jwt_resolver(verifier: JWTVerifier, *, plan_claim: str = "plan") -> CallerResolver:
    async def resolve(request: Request) -> Caller:
        authorization = request.headers.get("authorization", "")
        if not authorization:
            return anonymous(request)
        token = authorization.removeprefix("Bearer ").strip()
        access = await verifier.verify_token(token) if token else None
        subject = access.claims.get("sub") if access is not None else None
        if access is None or not isinstance(subject, str) or not subject:
            raise UnauthenticatedError("Token no válido.")
        return Caller(f"sub:{subject}", plan_from_claim(access.claims.get(plan_claim)))

    return resolve


def build_verifier(settings: EdgeSettings) -> JWTVerifier:
    if settings.jwks_uri and settings.public_key_file:
        raise ValueError("Configura EDGE__JWKS_URI (nube) o EDGE__PUBLIC_KEY_FILE, no ambos.")
    public_key: str | None = None
    if settings.public_key_file:
        public_key = Path(settings.public_key_file).read_text(encoding="utf-8")
    elif not settings.jwks_uri:
        if get_settings().environment != "local":
            raise ValueError("Fuera de local, EDGE__JWKS_URI es obligatorio.")
        # Local: la clave pública del emisor de desarrollo (se crea si no existe).
        public_key = DevTokenIssuer.load_or_create(
            Path(settings.dev_private_key_file), issuer=settings.issuer
        ).public_key
    return JWTVerifier(
        public_key=public_key,
        jwks_uri=settings.jwks_uri or None,
        issuer=settings.issuer,
        audience=settings.audience,
        algorithm="RS256",
    )


def build_resolver(settings: EdgeSettings) -> CallerResolver:
    if settings.auth == "none":

        async def resolve(request: Request) -> Caller:
            return anonymous(request)

        return resolve
    return jwt_resolver(build_verifier(settings), plan_claim=settings.plan_claim)

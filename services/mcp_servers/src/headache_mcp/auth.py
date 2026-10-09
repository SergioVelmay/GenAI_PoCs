"""OAuth 2.1 en MCP: cada servidor es un *resource server* que valida bearer tokens (JWT).

- El authorization server es externo: Entra ID (Azure) o Cognito (AWS). En local, `DevTokenIssuer`
  firma tokens con una clave RSA de desarrollo (nunca en la nube).
- Se valida firma, `iss`, `exp`, `aud` (uno por servidor: sin *token passthrough*) y scopes.
- La identidad del cliente sale de un claim del token, nunca de los argumentos de la tool.
- Los metadatos RFC 9728 (`/.well-known/oauth-protected-resource/mcp`) dicen a los clientes
  qué authorization server y qué scopes necesitan.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fastmcp.server.auth import AuthProvider, RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair
from fastmcp.server.dependencies import get_access_token
from pydantic import AnyHttpUrl, SecretStr

from headache_core.application.tools import ToolContext, ToolError
from headache_core.config import McpSettings, get_settings
from headache_mcp.adapter import IdentityResolver

SERVER_SCOPES: dict[str, list[str]] = {
    "catalog": ["catalog:read"],
    "ticketing": ["orders:read"],
    "refunds": ["refunds:write"],
}


def token_identity(claim: str = "sub") -> IdentityResolver:
    def resolve() -> ToolContext:
        token = get_access_token()
        customer = token.claims.get(claim) if token is not None else None
        if not isinstance(customer, str) or not customer:
            raise ToolError("Petición no autenticada.", kind="unauthenticated")
        return ToolContext(customer_id=customer)

    return resolve


def jwt_auth(settings: McpSettings, server: str, *, public_url: str) -> AuthProvider:
    if bool(settings.jwks_uri) == bool(settings.public_key_file):
        raise ValueError("Configura MCP__JWKS_URI (nube) o MCP__PUBLIC_KEY_FILE (local), no ambos.")
    public_key = (
        Path(settings.public_key_file).read_text(encoding="utf-8")
        if settings.public_key_file
        else None
    )
    verifier = JWTVerifier(
        public_key=public_key,
        jwks_uri=settings.jwks_uri or None,
        issuer=settings.issuer,
        audience=settings.audience(server),
        algorithm="RS256",
        required_scopes=SERVER_SCOPES[server],
    )
    return RemoteAuthProvider(
        verifier,
        authorization_servers=[AnyHttpUrl(settings.issuer)],
        base_url=public_url,
        resource_name=f"headache-{server}",
    )


class DevTokenIssuer:
    """Authorization server de juguete para desarrollo local: firma tokens con una clave RSA."""

    def __init__(self, keys: RSAKeyPair, *, issuer: str) -> None:
        self._keys = keys
        self.issuer = issuer

    @property
    def public_key(self) -> str:
        return self._keys.public_key

    @classmethod
    def load_or_create(cls, private_key_file: Path, *, issuer: str) -> DevTokenIssuer:
        public_file = private_key_file.with_suffix(".pub")
        if private_key_file.exists():
            keys = RSAKeyPair(
                private_key=SecretStr(private_key_file.read_text(encoding="utf-8")),
                public_key=public_file.read_text(encoding="utf-8"),
            )
        else:
            keys = RSAKeyPair.generate()
            private_key_file.parent.mkdir(parents=True, exist_ok=True)
            private_key_file.write_text(keys.private_key.get_secret_value(), encoding="utf-8")
            public_file.write_text(keys.public_key, encoding="utf-8")
        return cls(keys, issuer=issuer)

    def mint(
        self, *, subject: str, audience: str, scopes: list[str], expires_in_seconds: int = 900
    ) -> str:
        return self._keys.create_token(
            subject=subject,
            issuer=self.issuer,
            audience=audience,
            scopes=scopes,
            expires_in_seconds=expires_in_seconds,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Emisor de tokens de desarrollo (solo local)")
    parser.add_argument("--customer", required=True)
    parser.add_argument("--server", choices=sorted(SERVER_SCOPES), required=True)
    args = parser.parse_args()

    settings = get_settings()
    if settings.environment != "local":
        raise SystemExit("El emisor de desarrollo solo existe en local.")
    issuer = DevTokenIssuer.load_or_create(
        Path(settings.mcp.dev_private_key_file), issuer=settings.mcp.issuer
    )
    print(
        issuer.mint(
            subject=args.customer,
            audience=settings.mcp.audience(args.server),
            scopes=SERVER_SCOPES[args.server],
        )
    )


if __name__ == "__main__":
    main()

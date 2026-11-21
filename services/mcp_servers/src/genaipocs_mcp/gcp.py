"""Token por servidor en GCP para el MCP Gateway (fase 6.G2): `signJwt` de IAM Credentials.

GCP no tiene un On-Behalf-Of como Entra ID ni un token exchange como AgentCore Identity: Google
STS solo cambia un token externo por un *access token* de Google (federación), que no lleva el
audience ni el scope de un servidor nuestro. Así que el gateway hace de STS **sin claves**:

1. Valida el ID token del usuario (Identity Platform; el `customer_id` de Genaipocs viaja como
   *custom claim*, que el backend fija una vez con el Admin SDK al dar de alta al cliente).
2. Para cada servidor, pide a IAM Credentials que firme con la clave gestionada por Google de
   **su** cuenta de servicio un JWT con `aud` y `scope` de ese servidor, `sub` = cliente y
   `act` = el gateway (RFC 8693: quién actúa en nombre de quién queda en el propio token).
3. Cada servidor lo valida con el JWKS público de esa cuenta de servicio
   (`MCP__ISSUER` = correo de la cuenta, `MCP__JWKS_URI` = `service_account_jwks_uri(...)`).

Nunca se reenvía el token de entrada (sin *token passthrough*). La firma necesita
`iam.serviceAccounts.signJwt` sobre la cuenta (Terraform: `infra/terraform/gcp/mcp_gateway.tf`).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol
from urllib.parse import quote

import httpx

from genaipocs_core.config import GcpSettings, McpSettings

if TYPE_CHECKING:
    from google.auth.credentials import Credentials

IAM_CREDENTIALS_URL = "https://iamcredentials.googleapis.com"
# Límite de `signJwt`: `exp` como mucho 12 h por delante (y la clave se publica al menos 12 h).
MAX_TTL_S = 12 * 3600
IDENTITY_PLATFORM_JWKS_URI = (
    "https://www.googleapis.com/service_accounts/v1/metadata/jwk/"
    "securetoken@system.gserviceaccount.com"
)


def service_account_jwks_uri(email: str) -> str:
    """JWKS público de una cuenta de servicio: lo que configuran los servidores MCP."""
    return f"https://www.googleapis.com/service_accounts/v1/metadata/jwk/{quote(email, safe='@')}"


def identity_platform_issuer(project: str) -> str:
    """`iss` de los ID tokens de Identity Platform (su `aud` es el ID del proyecto)."""
    return f"https://securetoken.google.com/{project}"


class TokenExchangeError(RuntimeError):
    """IAM Credentials no firmó el token (permisos, cuenta inexistente, cuota...)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class JwtSigner(Protocol):
    async def sign_jwt(self, service_account: str, payload: str) -> str:
        """Devuelve el JWT firmado (cabecera con `kid` generada por Google + payload + firma)."""
        ...


class IamCredentialsSigner:
    """`projects/-/serviceAccounts/{cuenta}:signJwt` por REST con las credenciales de ADC.

    Sin SDK nuevo: `google-auth` (ya en el lock) da el token y `httpx` hace la llamada.
    """

    def __init__(
        self,
        credentials: Credentials,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = IAM_CREDENTIALS_URL,
    ) -> None:
        self._credentials = credentials
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._base_url = base_url.rstrip("/")

    async def _access_token(self) -> str:
        if not self._credentials.valid:
            from google.auth.transport.requests import Request

            # `refresh` es síncrono (requests): fuera del bucle de eventos.
            await asyncio.to_thread(self._credentials.refresh, Request())
        token = self._credentials.token
        if not token:
            raise TokenExchangeError("ADC no devolvió un token de acceso.")
        return str(token)

    async def sign_jwt(self, service_account: str, payload: str) -> str:
        # El comodín `-` es obligatorio: un ID de proyecto en su lugar da 400.
        url = f"{self._base_url}/v1/projects/-/serviceAccounts/{quote(service_account)}:signJwt"
        headers = {"Authorization": f"Bearer {await self._access_token()}"}
        try:
            response = await self._client.post(url, json={"payload": payload}, headers=headers)
        except httpx.HTTPError as exc:
            raise TokenExchangeError(f"IAM Credentials no responde: {exc}") from exc
        if response.status_code != 200:
            raise TokenExchangeError(
                f"signJwt de {service_account} devolvió {response.status_code}",
                status=response.status_code,
            )
        signed = response.json().get("signedJwt")
        if not isinstance(signed, str) or not signed:
            raise TokenExchangeError("signJwt no devolvió signedJwt.")
        return signed

    async def aclose(self) -> None:
        await self._client.aclose()


class GcpTokenExchanger:
    """`TokenExchanger` de GCP: un JWT nuevo por servidor firmado por la cuenta del gateway."""

    def __init__(
        self,
        signer: JwtSigner,
        service_account: str,
        *,
        ttl_s: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not service_account:
            raise ValueError("Falta MCP__SIGNER_SERVICE_ACCOUNT (cuenta de servicio del gateway).")
        if not 0 < ttl_s <= MAX_TTL_S:
            raise ValueError(f"signJwt admite como mucho {MAX_TTL_S} s de vida.")
        self._signer = signer
        self._service_account = service_account
        self._ttl_s = ttl_s
        self._clock = clock

    @property
    def issuer(self) -> str:
        """`iss` de los tokens emitidos: lo que deben configurar los servidores en MCP__ISSUER."""
        return self._service_account

    @classmethod
    def from_settings(cls, mcp: McpSettings, gcp: GcpSettings) -> GcpTokenExchanger:
        import google.auth

        from genaipocs_core.adapters.gcp_auth import CLOUD_PLATFORM_SCOPE, gcp_credentials

        if not mcp.signer_service_account:
            # Antes que ADC: el error de configuración es el que hay que ver primero.
            raise ValueError("Falta MCP__SIGNER_SERVICE_ACCOUNT (cuenta de servicio del gateway).")
        credentials = gcp_credentials(gcp)
        if credentials is None:
            credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        return cls(
            IamCredentialsSigner(credentials),
            mcp.signer_service_account,
            ttl_s=mcp.exchange_ttl_s,
        )

    def claims(self, *, subject: str, audience: str, scopes: Sequence[str]) -> dict[str, object]:
        now = int(self._clock())
        return {
            "iss": self._service_account,
            "sub": subject,
            "aud": audience,
            "scope": " ".join(scopes),
            "iat": now,
            "exp": now + self._ttl_s,
            "jti": uuid.uuid4().hex,
            # RFC 8693 §4.1: el actor es el gateway; el sujeto, el cliente de Genaipocs.
            "act": {"sub": self._service_account},
        }

    async def exchange(
        self, *, subject: str, assertion: str, audience: str, scopes: Sequence[str]
    ) -> str:
        # `assertion` ya la validó el gateway (Identity Platform): aquí solo se usa el sujeto.
        if not subject:
            raise ValueError("El token de entrada no trae cliente.")
        if not scopes:
            raise ValueError("El token de un servidor necesita al menos un scope.")
        payload = self.claims(subject=subject, audience=audience, scopes=scopes)
        return await self._signer.sign_jwt(self._service_account, json.dumps(payload))

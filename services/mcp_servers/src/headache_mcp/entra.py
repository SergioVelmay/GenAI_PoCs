"""Token exchange con Entra ID On-Behalf-Of (OBO) para el MCP Gateway en Azure (fase 14).

El gateway recibe el token del usuario (audience del gateway) y pide a Entra un token NUEVO
para cada servidor MCP en nombre de ese usuario: audience = App ID URI del servidor y solo su
scope. Nunca se reenvía el token de entrada (sin *token passthrough*).

Requisitos en Entra (aplazados a la fase 14, como código con el proveedor `azuread`):
- Una App Registration por servidor MCP y otra para el gateway, cada una con su App ID URI
  (`api://headache-mcp/<servidor>`) y sus scopes (`orders:read`...).
- El gateway, cliente confidencial con secreto o certificado (en Container Apps, credencial
  federada con la identidad gestionada) y permiso delegado sobre los scopes de los servidores.
- El `customer_id` de Headache no está en el token de Entra: claim opcional desde una
  extensión de directorio o tabla de mapeo `oid` -> cliente en el backend.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol


class _AccessToken(Protocol):
    @property
    def token(self) -> str: ...


class OboCredential(Protocol):
    """Subconjunto de `azure.identity.aio.OnBehalfOfCredential` que usa el exchanger."""

    async def get_token(self, *scopes: str) -> _AccessToken: ...

    async def close(self) -> None: ...


def entra_scopes(audience: str, scopes: Sequence[str]) -> list[str]:
    """Entra pide los scopes calificados con el recurso: `<App ID URI>/<scope>`."""
    if not scopes:
        raise ValueError("OBO necesita al menos un scope")
    return [f"{audience.rstrip('/')}/{scope}" for scope in scopes]


class EntraOboExchanger:
    """`TokenExchanger` real: un `OnBehalfOfCredential` por petición (cada uno con su aserción)."""

    def __init__(self, credential_for: Callable[[str], OboCredential]) -> None:
        self._credential_for = credential_for

    @classmethod
    def from_client_secret(
        cls, *, tenant_id: str, client_id: str, client_secret: str
    ) -> EntraOboExchanger:
        from azure.identity.aio import OnBehalfOfCredential

        def credential_for(assertion: str) -> OboCredential:
            return OnBehalfOfCredential(
                tenant_id, client_id, client_secret=client_secret, user_assertion=assertion
            )

        return cls(credential_for)

    async def exchange(
        self, *, subject: str, assertion: str, audience: str, scopes: Sequence[str]
    ) -> str:
        if not assertion:
            raise ValueError("OBO necesita el token de entrada del usuario")
        credential = self._credential_for(assertion)
        try:
            token = await credential.get_token(*entra_scopes(audience, scopes))
        finally:
            await credential.close()
        return token.token

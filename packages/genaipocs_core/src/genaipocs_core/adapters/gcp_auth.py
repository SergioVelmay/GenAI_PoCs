"""Credenciales de GCP sin secretos, compartidas por todos los adaptadores de la pasada GCP.

Equivalente de `DefaultAzureCredential` (Azure) y de la cadena de boto3 (AWS): ADC resuelve la
identidad del entorno (usuario de `gcloud auth application-default login` en local, cuenta de
servicio adjunta en la nube, Workload Identity Federation en la CI). En local, además, se
*suplanta* la cuenta de servicio de la aplicación (`GCP__IMPERSONATE_SERVICE_ACCOUNT`): se prueba
con sus permisos exactos, no con los del desarrollador, y sin claves JSON.
"""

from __future__ import annotations

import google.auth
from google.auth import impersonated_credentials
from google.auth.credentials import Credentials

from genaipocs_core.config import GcpSettings

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def gcp_credentials(settings: GcpSettings) -> Credentials | None:
    """None = que el SDK resuelva ADC él mismo; con impersonation, un token de 1 h de la cuenta
    de servicio firmado a partir de la identidad de ADC (necesita *Token Creator* sobre ella)."""
    if not settings.impersonate_service_account:
        return None
    source, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
    return impersonated_credentials.Credentials(  # type: ignore[no-untyped-call]
        source_credentials=source,
        target_principal=settings.impersonate_service_account,
        target_scopes=[CLOUD_PLATFORM_SCOPE],
    )


def google_id_token(settings: GcpSettings, audience: str) -> str:
    """ID token de Google (OIDC) para llamar a un servicio propio (A2A en Cloud Run, 9.G4).

    Síncrono (`requests`). En Cloud Run, el servidor de metadatos con la cuenta de servicio
    adjunta (`format=full`: incluye `email`). En local, *impersonation* de
    `GCP__IMPERSONATE_SERVICE_ACCOUNT` con `include_email` (sin él, el token no trae `email` y
    el especialista lo rechaza). Un usuario de ADC no puede emitir ID tokens con audience propio.
    """
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    request = Request()
    target = gcp_credentials(settings)
    if target is None:
        return str(id_token.fetch_id_token(request, audience))  # type: ignore[no-untyped-call]
    credentials = impersonated_credentials.IDTokenCredentials(  # type: ignore[no-untyped-call]
        target, target_audience=audience, include_email=True
    )
    credentials.refresh(request)
    return str(credentials.token)

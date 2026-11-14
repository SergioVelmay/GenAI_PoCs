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

from headache_core.config import GcpSettings

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

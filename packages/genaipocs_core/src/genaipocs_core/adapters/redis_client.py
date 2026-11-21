"""Cliente del Redis de escala (fase 10), compartido por el rate limiter y la caché semántica.

Un solo sitio para conectar con Redis 8 (local) o con un Valkey gestionado (10.G2):

- **Fallar rápido**: timeouts cortos y sin reintentos. redis-py reintenta 3 veces con backoff por
  defecto (~7 s medidos con Redis parado); para un limitador o una caché, el llamante prefiere
  fallar pronto y aplicar su plan B (*fail-open*, saltarse la caché).
- **Memorystore for Valkey con IAM auth** (`SCALE_REDIS__AUTH=gcp_iam`): la contraseña de `AUTH`
  es un token de acceso de Google, válido 1 h. El *credential provider* lo pide (o lo renueva)
  cada vez que el pool abre una conexión; la identidad necesita el rol
  `roles/memorystore.dbConnectionUser`.
  Es el equivalente de Entra ID en Azure Managed Redis o de la autenticación IAM de ElastiCache.
- **TLS** (`SCALE_REDIS__TLS`) con la CA del servidor (`SCALE_REDIS__TLS_CA_FILE`): Memorystore
  la publica en la instancia (*server CA*). Con IAM auth, TLS evita que el token viaje en claro.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.credentials import CredentialProvider

from genaipocs_core.config import GcpSettings, ScaleRedisSettings

if TYPE_CHECKING:
    from google.auth.credentials import Credentials


class GoogleIamCredentialProvider(CredentialProvider):
    """Token de acceso de ADC (o de la cuenta suplantada) como contraseña de `AUTH`."""

    def __init__(self, credentials: Credentials) -> None:
        self._credentials = credentials
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, gcp: GcpSettings) -> GoogleIamCredentialProvider:
        import google.auth

        from genaipocs_core.adapters.gcp_auth import CLOUD_PLATFORM_SCOPE, gcp_credentials

        credentials = gcp_credentials(gcp)
        if credentials is None:
            credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        return cls(credentials)

    def get_credentials(self) -> tuple[str]:
        from google.auth.transport.requests import Request

        with self._lock:
            if not self._credentials.valid:
                self._credentials.refresh(Request())  # type: ignore[no-untyped-call]
            return (str(self._credentials.token),)

    async def get_credentials_async(self) -> tuple[str]:
        # El refresco es síncrono (`requests`): fuera del bucle de eventos.
        return await asyncio.to_thread(self.get_credentials)


def scale_redis_client(settings: ScaleRedisSettings, gcp: GcpSettings | None = None) -> Redis:
    options: dict[str, Any] = {}
    if settings.auth == "gcp_iam":
        options["credential_provider"] = GoogleIamCredentialProvider.from_settings(
            gcp or GcpSettings()
        )
    else:
        options["password"] = settings.password.get_secret_value() or None
    if settings.tls:
        options["ssl"] = True
        options["ssl_ca_certs"] = settings.tls_ca_file or None
    return Redis(
        host=settings.host,
        port=settings.port,
        db=settings.db,
        socket_timeout=settings.timeout_seconds,
        socket_connect_timeout=settings.timeout_seconds,
        retry=Retry(NoBackoff(), 0),
        max_connections=settings.max_connections,
        # RESP2: redis-py 8 habla RESP3 por defecto y Valkey contesta a `FT.SEARCH` con un
        # array aunque se negocie RESP3 (el parser de RESP3 espera un mapa y se rompe; medido en
        # la 10.G2). Con RESP2 los dos motores responden igual.
        protocol=2,
        **options,
    )

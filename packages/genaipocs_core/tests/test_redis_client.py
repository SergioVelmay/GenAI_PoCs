"""Cliente del Redis de escala (10.G2): contraseña o IAM de Google, TLS y RESP2, sin red."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from genaipocs_core.adapters.redis_client import GoogleIamCredentialProvider, scale_redis_client
from genaipocs_core.config import ScaleRedisSettings


def connection_kwargs(settings: ScaleRedisSettings, **kwargs: Any) -> dict[str, Any]:
    client = scale_redis_client(settings, **kwargs)
    return dict(client.connection_pool.connection_kwargs)


def test_password_auth_by_default_with_resp2_and_no_retries() -> None:
    options = connection_kwargs(ScaleRedisSettings(password=SecretStr("s3cret")))

    assert options["password"] == "s3cret"
    assert options.get("credential_provider") is None
    # RESP2: Valkey contesta a FT.SEARCH con un array aunque se negocie RESP3.
    assert options["protocol"] == 2
    assert options["socket_timeout"] == 0.25


class FakeCredentials:
    """Lo que el provider usa de `google.auth.credentials.Credentials`."""

    def __init__(self) -> None:
        self.valid = False
        self.token: str | None = None
        self.refreshes = 0

    def refresh(self, request: object) -> None:
        self.refreshes += 1
        self.token = f"ya29.token-{self.refreshes}"
        self.valid = True


async def test_iam_provider_uses_the_access_token_as_password_and_refreshes_it() -> None:
    credentials = FakeCredentials()
    provider = GoogleIamCredentialProvider(credentials)  # type: ignore[arg-type]

    assert await provider.get_credentials_async() == ("ya29.token-1",)
    assert provider.get_credentials() == ("ya29.token-1",)  # Vigente: no se pide otro.

    credentials.valid = False  # Caducado (1 h): la siguiente conexión pide uno nuevo.
    assert provider.get_credentials() == ("ya29.token-2",)


def test_gcp_iam_auth_uses_the_provider_and_no_password(monkeypatch: Any) -> None:
    fake = GoogleIamCredentialProvider(FakeCredentials())  # type: ignore[arg-type]
    monkeypatch.setattr(GoogleIamCredentialProvider, "from_settings", lambda gcp: fake)

    options = connection_kwargs(ScaleRedisSettings(auth="gcp_iam", password=SecretStr("ignorada")))

    assert options["credential_provider"] is fake
    assert options.get("password") is None


def test_tls_with_the_server_ca() -> None:
    client = scale_redis_client(ScaleRedisSettings(tls=True, tls_ca_file="server-ca.pem"))

    options = client.connection_pool.connection_kwargs
    assert options["ssl_ca_certs"] == "server-ca.pem"
    assert client.connection_pool.connection_class.__name__ == "SSLConnection"

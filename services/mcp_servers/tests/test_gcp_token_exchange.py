"""Fase 6.G2: token por servidor en GCP con `signJwt` e Identity Platform como IdP de entrada.

Sin red: un firmador con clave RSA local hace de IAM Credentials (misma forma de JWT, con `kid`)
y los JWKS se sirven desde memoria con las URLs reales de Google. Los servidores y el gateway
validan con el `JWTVerifier` de verdad.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.tests import ASGIServer, asgi_server
from joserfc import jwt
from joserfc.jwk import RSAKey

from genaipocs_core.adapters.memory import InMemoryUnitOfWork
from genaipocs_core.adapters.ticketing_local import UnitOfWorkTicketing
from genaipocs_core.config import McpGatewayAuthSettings, McpSettings
from genaipocs_core.domain import Customer, Event, EventCategory, Money, Order, OrderStatus, Session
from genaipocs_mcp.adapter import anonymous
from genaipocs_mcp.auth import SERVER_SCOPES, gateway_auth, jwt_auth, token_identity
from genaipocs_mcp.gateway import create_gateway
from genaipocs_mcp.gcp import (
    IDENTITY_PLATFORM_JWKS_URI,
    MAX_TTL_S,
    GcpTokenExchanger,
    IamCredentialsSigner,
    TokenExchangeError,
    identity_platform_issuer,
    service_account_jwks_uri,
)
from genaipocs_mcp.servers import create_server

PROJECT = "genaipocs-dev"
GATEWAY_SA = f"genaipocs-dev-mcp-gw@{PROJECT}.iam.gserviceaccount.com"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PRICE = Money(Decimal("30.00"), "EUR")
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


class KeySigner:
    """Hace de `signJwt`: firma el payload tal cual con RS256 y un `kid`, como Google."""

    def __init__(self, kid: str) -> None:
        self.kid = kid
        self.key = RSAKey.generate_key(2048)
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def sign_jwt(self, service_account: str, payload: str) -> str:
        claims = json.loads(payload)
        self.requests.append((service_account, claims))
        return self.sign(claims)

    def sign(self, claims: dict[str, Any]) -> str:
        return jwt.encode({"alg": "RS256", "kid": self.kid, "typ": "JWT"}, claims, self.key)

    def jwks(self) -> dict[str, Any]:
        public = self.key.as_dict(private=False)
        return {"keys": [{**public, "kid": self.kid, "alg": "RS256", "use": "sig"}]}


@pytest.fixture
def gateway_key() -> KeySigner:
    return KeySigner("gw-key-1")


@pytest.fixture
def securetoken() -> KeySigner:
    return KeySigner("securetoken-key-1")


@pytest.fixture(autouse=True)
def google_jwks(
    monkeypatch: pytest.MonkeyPatch, gateway_key: KeySigner, securetoken: KeySigner
) -> None:
    """Los JWKS públicos de la cuenta del gateway y de Identity Platform, sin red."""
    published = {
        service_account_jwks_uri(GATEWAY_SA): gateway_key.jwks(),
        IDENTITY_PLATFORM_JWKS_URI: securetoken.jwks(),
    }

    async def fetch(self: JWTVerifier) -> dict[str, Any]:
        assert self.jwks_uri is not None
        return published[self.jwks_uri]

    monkeypatch.setattr(JWTVerifier, "_fetch_jwks", fetch)


@pytest.fixture
def ticketing() -> UnitOfWorkTicketing:
    uow = InMemoryUnitOfWork(
        customers=[
            Customer("cus_1", "Ana Pérez", "ana@example.com", "ES", "es"),
            Customer("cus_2", "Bob Smith", "bob@example.com", "GB", "en"),
        ],
        events=[
            Event(
                "evt_1",
                "Candlelit Strings: Vivaldi",
                EventCategory.CANDLELIT,
                "Madrid",
                "ven_1",
                "Concierto a la luz de las velas.",
                PRICE,
                refundable=True,
                refund_window_hours=48,
            )
        ],
        sessions=[Session("ses_1", "evt_1", datetime(2026, 11, 20, 20, tzinfo=UTC), 100, 3)],
        orders=[
            Order("ord_1", "cus_1", "ses_1", 2, PRICE * 2, OrderStatus.PAID, NOW),
            Order("ord_2", "cus_2", "ses_1", 1, PRICE, OrderStatus.PAID, NOW),
        ],
    )
    return UnitOfWorkTicketing(lambda: uow, clock=lambda: NOW)


def _settings(**gateway: Any) -> McpSettings:
    """Servidores que confían en la cuenta del gateway; gateway que confía en Identity Platform."""
    return McpSettings(
        auth="jwt",
        issuer=GATEWAY_SA,
        jwks_uri=service_account_jwks_uri(GATEWAY_SA),
        authorization_server=identity_platform_issuer(PROJECT),
        exchanger="gcp",
        signer_service_account=GATEWAY_SA,
        gateway_auth=McpGatewayAuthSettings(
            **{
                "issuer": identity_platform_issuer(PROJECT),
                "jwks_uri": IDENTITY_PLATFORM_JWKS_URI,
                "audience": PROJECT,
                "customer_claim": "customer_id",
                "require_scopes": False,
                **gateway,
            }
        ),
    )


def _id_token(securetoken: KeySigner, **claims: Any) -> str:
    """ID token con la forma de Identity Platform: `aud` = proyecto, sin scopes."""
    now = int(time.time())
    return securetoken.sign(
        {
            "iss": identity_platform_issuer(PROJECT),
            "aud": PROJECT,
            "sub": "uid-ana",
            "auth_time": now,
            "iat": now,
            "exp": now + 3600,
            "customer_id": "cus_1",
            "firebase": {"sign_in_provider": "password"},
            **claims,
        }
    )


# ---------- GcpTokenExchanger ----------


async def test_exchanged_token_is_valid_only_on_its_server(gateway_key: KeySigner) -> None:
    exchanger = GcpTokenExchanger(gateway_key, GATEWAY_SA, ttl_s=300, clock=lambda: 1_800_000_000)
    settings = _settings()

    token = await exchanger.exchange(
        subject="cus_1",
        assertion="id-token",
        audience=settings.audience("refunds"),
        scopes=SERVER_SCOPES["refunds"],
    )

    [(account, claims)] = gateway_key.requests
    assert account == GATEWAY_SA
    assert claims["iss"] == exchanger.issuer == GATEWAY_SA
    assert (claims["sub"], claims["aud"], claims["scope"]) == (
        "cus_1",
        "api://genaipocs-mcp/refunds",
        "refunds:write",
    )
    assert claims["exp"] - claims["iat"] == 300
    # RFC 8693: el token dice quién actúa (el gateway) y en nombre de quién (el cliente).
    assert claims["act"] == {"sub": GATEWAY_SA}
    assert "id-token" not in json.dumps(claims)  # sin passthrough del token de entrada

    refunds = jwt_auth(settings, "refunds", public_url="http://127.0.0.1")
    ticketing = jwt_auth(settings, "ticketing", public_url="http://127.0.0.1")
    accepted = await refunds.verify_token(token)
    assert accepted is not None
    assert accepted.scopes == ["refunds:write"]
    assert accepted.claims["sub"] == "cus_1"
    # El mismo token no sirve en otro servidor (audience distinto).
    assert await ticketing.verify_token(token) is None


async def test_every_exchange_gets_a_unique_token_id(gateway_key: KeySigner) -> None:
    exchanger = GcpTokenExchanger(gateway_key, GATEWAY_SA)
    for _ in range(2):
        await exchanger.exchange(subject="cus_1", assertion="t", audience="a", scopes=["s"])

    first, second = (claims["jti"] for _, claims in gateway_key.requests)
    assert first != second


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"subject": "", "scopes": ["orders:read"]}, "cliente"),
        ({"subject": "cus_1", "scopes": []}, "scope"),
    ],
)
async def test_exchange_refuses_tokens_without_customer_or_scope(
    gateway_key: KeySigner, kwargs: dict[str, Any], message: str
) -> None:
    exchanger = GcpTokenExchanger(gateway_key, GATEWAY_SA)

    with pytest.raises(ValueError, match=message):
        await exchanger.exchange(assertion="t", audience="a", **kwargs)
    assert gateway_key.requests == []


def test_exchanger_needs_an_account_and_a_ttl_that_signjwt_accepts(gateway_key: KeySigner) -> None:
    with pytest.raises(ValueError, match="SIGNER_SERVICE_ACCOUNT"):
        GcpTokenExchanger(gateway_key, "")
    with pytest.raises(ValueError, match="vida"):
        GcpTokenExchanger(gateway_key, GATEWAY_SA, ttl_s=MAX_TTL_S + 1)


def test_public_endpoints_of_google_identities() -> None:
    assert service_account_jwks_uri(GATEWAY_SA) == (
        "https://www.googleapis.com/service_accounts/v1/metadata/jwk/"
        "genaipocs-dev-mcp-gw@genaipocs-dev.iam.gserviceaccount.com"
    )
    assert identity_platform_issuer(PROJECT) == "https://securetoken.google.com/genaipocs-dev"


# ---------- IamCredentialsSigner (REST de IAM Credentials) ----------


class FakeCredentials:
    def __init__(self, *, valid: bool) -> None:
        self.valid = valid
        self.token: str | None = "adc-token" if valid else None
        self.refreshed = 0

    def refresh(self, request: object) -> None:
        self.refreshed += 1
        self.token, self.valid = "refreshed-token", True


async def test_signer_calls_signjwt_with_the_wildcard_project_and_adc_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"keyId": "k1", "signedJwt": "a.b.c"})

    credentials = FakeCredentials(valid=False)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    signer = IamCredentialsSigner(credentials, client=client)  # type: ignore[arg-type]

    assert await signer.sign_jwt(GATEWAY_SA, '{"sub": "cus_1"}') == "a.b.c"
    await signer.aclose()

    [request] = seen
    assert request.url.path == f"/v1/projects/-/serviceAccounts/{GATEWAY_SA}:signJwt"
    assert request.headers["Authorization"] == "Bearer refreshed-token"
    assert json.loads(request.content) == {"payload": '{"sub": "cus_1"}'}
    assert credentials.refreshed == 1


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}}), 403),
        (httpx.Response(200, json={"keyId": "k1"}), None),
    ],
)
async def test_signer_failures_are_typed(response: httpx.Response, status: int | None) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response))
    signer = IamCredentialsSigner(FakeCredentials(valid=True), client=client)  # type: ignore[arg-type]

    with pytest.raises(TokenExchangeError) as error:
        await signer.sign_jwt(GATEWAY_SA, "{}")
    assert error.value.status == status


# ---------- Gateway con Identity Platform de entrada y signJwt hacia los servidores ----------


@asynccontextmanager
async def _gcp_gateway(
    ticketing: UnitOfWorkTicketing, exchanger: GcpTokenExchanger, settings: McpSettings
) -> AsyncIterator[ASGIServer]:
    async with AsyncExitStack() as stack:
        backends = {}
        for name in SERVER_SCOPES:
            server = create_server(
                name,
                ticketing,
                identity=anonymous if name == "catalog" else token_identity(),
                auth=jwt_auth(settings, name, public_url="http://127.0.0.1"),
            )
            backends[name] = await stack.enter_async_context(asgi_server(server))
        gateway = create_gateway(
            list(backends),
            settings=settings,
            connect=lambda name, token: backends[name].client(auth=token),
            exchanger=exchanger,
            auth=gateway_auth(settings, public_url="http://127.0.0.1"),
            requests_per_second=50.0,
        )
        yield await stack.enter_async_context(asgi_server(gateway))


async def test_gateway_turns_an_identity_platform_user_into_per_server_tokens(
    ticketing: UnitOfWorkTicketing, gateway_key: KeySigner, securetoken: KeySigner
) -> None:
    settings = _settings()
    exchanger = GcpTokenExchanger(gateway_key, GATEWAY_SA)

    async with (
        _gcp_gateway(ticketing, exchanger, settings) as running,
        running.client(auth=_id_token(securetoken)) as client,
    ):
        mine = await client.call_tool("list_my_orders", {"limit": 5})
        foreign = await client.call_tool("get_order", {"order_id": "ord_2"}, raise_on_error=False)

    assert mine.structured_content is not None
    assert [o["id"] for o in mine.structured_content["items"]] == ["ord_1"]
    assert foreign.is_error
    # El cliente sale del custom claim (no del uid de Identity Platform) y cada servidor recibe
    # un token con su audience y solo su scope, firmado por la cuenta del gateway.
    issued = {(c["sub"], c["aud"], c["scope"]) for _, c in gateway_key.requests}
    assert ("cus_1", "api://genaipocs-mcp/ticketing", "orders:read") in issued
    assert {sub for sub, _, _ in issued} == {"cus_1"}
    assert {account for account, _ in gateway_key.requests} == {GATEWAY_SA}


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "otro-proyecto"},  # ID token de otro proyecto de Identity Platform
        {"iss": identity_platform_issuer("otro-proyecto")},
        {"exp": int(time.time()) - 60},
    ],
)
async def test_gateway_rejects_id_tokens_of_other_projects_or_expired(
    claims: dict[str, Any],
    ticketing: UnitOfWorkTicketing,
    gateway_key: KeySigner,
    securetoken: KeySigner,
) -> None:
    token = _id_token(securetoken, **claims)

    async with (
        _gcp_gateway(ticketing, GcpTokenExchanger(gateway_key, GATEWAY_SA), _settings()) as running,
        running.http_client() as http,
    ):
        response = await http.post(
            "/mcp", json=INIT, headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 401
    assert gateway_key.requests == []


async def test_a_user_without_customer_claim_gets_no_server_token(
    ticketing: UnitOfWorkTicketing, gateway_key: KeySigner, securetoken: KeySigner
) -> None:
    """Usuario autenticado que aún no es cliente (el backend no le puso el claim)."""
    token = _id_token(securetoken, customer_id=None)

    async with (
        _gcp_gateway(ticketing, GcpTokenExchanger(gateway_key, GATEWAY_SA), _settings()) as running,
        running.client(auth=token) as client,
    ):
        result = await client.call_tool("list_my_orders", {"limit": 5}, raise_on_error=False)

    assert result.is_error
    assert gateway_key.requests == []


async def test_servers_advertise_identity_platform_as_their_authorization_server(
    ticketing: UnitOfWorkTicketing,
) -> None:
    """El `iss` es el correo de la cuenta (no una URL): RFC 9728 anuncia dónde se autentica
    el usuario."""
    server = create_server(
        "refunds",
        ticketing,
        identity=token_identity(),
        auth=jwt_auth(_settings(), "refunds", public_url="http://127.0.0.1"),
    )

    async with asgi_server(server) as running, running.http_client() as http:
        metadata = (await http.get("/.well-known/oauth-protected-resource/mcp")).json()

    assert metadata["authorization_servers"] == ["https://securetoken.google.com/genaipocs-dev"]


def test_gateway_startup_picks_the_exchanger_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import google.auth

    from genaipocs_core.config import GcpSettings, Settings
    from genaipocs_mcp.__main__ import _exchanger

    def no_adc(**_: Any) -> Any:
        raise AssertionError("Sin cuenta configurada no se debe ni buscar ADC")

    monkeypatch.setattr(google.auth, "default", no_adc)
    with pytest.raises(ValueError, match="SIGNER_SERVICE_ACCOUNT"):
        _exchanger(Settings(_env_file=None, mcp=McpSettings(exchanger="gcp")))

    monkeypatch.setattr(google.auth, "default", lambda **_: (FakeCredentials(valid=True), PROJECT))
    settings = Settings(_env_file=None, mcp=_settings(), gcp=GcpSettings())
    exchanger = _exchanger(settings)
    assert isinstance(exchanger, GcpTokenExchanger)
    assert exchanger.issuer == GATEWAY_SA
    # El STS de desarrollo nunca fuera de local.
    with pytest.raises(SystemExit, match="solo existe en local"):
        _exchanger(Settings(_env_file=None, environment="pro"))


def test_gateway_auth_overrides_go_together() -> None:
    with pytest.raises(ValueError, match="van juntos"):
        gateway_auth(_settings(jwks_uri=""), public_url="http://127.0.0.1")
    with pytest.raises(ValueError, match="IdP de entrada propio"):
        gateway_auth(
            McpSettings(
                auth="jwt",
                jwks_uri="https://idp.test/jwks",
                gateway_auth=McpGatewayAuthSettings(audience=PROJECT),
            ),
            public_url="http://127.0.0.1",
        )

from dataclasses import dataclass, field

import pytest

from headache_mcp.entra import EntraOboExchanger, entra_scopes


@dataclass
class _Token:
    token: str


@dataclass
class FakeOboCredential:
    assertion: str
    requested: list[tuple[str, ...]] = field(default_factory=list)
    closed: bool = False

    async def get_token(self, *scopes: str) -> _Token:
        self.requested.append(scopes)
        return _Token(f"obo:{self.assertion}:{' '.join(scopes)}")

    async def close(self) -> None:
        self.closed = True


def test_scopes_are_qualified_with_the_server_app_id_uri() -> None:
    assert entra_scopes("api://headache-mcp/ticketing/", ["orders:read"]) == [
        "api://headache-mcp/ticketing/orders:read"
    ]
    with pytest.raises(ValueError):
        entra_scopes("api://headache-mcp/ticketing", [])


async def test_obo_uses_the_incoming_token_and_closes_the_credential() -> None:
    created: list[FakeOboCredential] = []

    def credential_for(assertion: str) -> FakeOboCredential:
        created.append(FakeOboCredential(assertion))
        return created[-1]

    exchanger = EntraOboExchanger(credential_for)
    token = await exchanger.exchange(
        subject="cus_1",
        assertion="user-token",
        audience="api://headache-mcp/refunds",
        scopes=["refunds:write"],
    )

    assert token == "obo:user-token:api://headache-mcp/refunds/refunds:write"
    [credential] = created
    assert credential.requested == [("api://headache-mcp/refunds/refunds:write",)]
    assert credential.closed
    with pytest.raises(ValueError, match="token de entrada"):
        await exchanger.exchange(subject="cus_1", assertion="", audience="a", scopes=["s"])


def test_real_credential_is_built_lazily_without_network() -> None:
    exchanger = EntraOboExchanger.from_client_secret(
        tenant_id="00000000-0000-0000-0000-000000000000", client_id="c", client_secret="s"
    )
    assert isinstance(exchanger, EntraOboExchanger)

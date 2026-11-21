"""A2A con ID tokens de Google (9.G4): `A2A__IDENTITY=gcp`, sin red ni nube.

Los tokens "tipo Google" se firman con una clave local y tienen los claims reales de un ID token
de una cuenta de servicio (`iss` de Google, `aud` = *custom audience*, `email`, `email_verified`,
`sub` numérico, sin `scope`). La verificación contra el JWKS de Google es la misma ruta de
`JWTVerifier`, con la clave pública en lugar de la URL.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair

from genaipocs_core.config import A2ASettings, Settings
from genaipocs_core.domain import CaseEnvelope, CaseStatus, Specialist, SupportIntent
from genaipocs_core.ports import ToolCall
from orchestrator.bootstrap import a2a_token_source
from orchestrator.remote import (
    BearerAuth,
    CallerPolicy,
    RemoteSpecialist,
    build_verifier,
    caller_policy,
    create_specialist_app,
    google_id_token_source,
)
from orchestrator.remote.auth import GOOGLE_ISSUERS, GOOGLE_JWKS_URI
from orchestrator.specialists import build_specialists
from orchestrator.testing import ORDER, local_world

ORCHESTRATOR = "genaipocs-dev-app@genaipocs-test.iam.gserviceaccount.com"
GCP = A2ASettings(identity="gcp", allowed_callers=[ORCHESTRATOR])
REFUNDS_AUDIENCE = "api://genaipocs-agents/refunds"
URL = "https://genaipocs-dev-a2a-refunds-123456789012.europe-west4.run.app"
PENDING = "Tu solicitud queda pendiente de revisión por un agente de Genaipocs."
REFUND_CASE = CaseEnvelope(
    case_id="case_1",
    email_id="eml_1",
    customer_id="cus_1",
    specialist=Specialist.REFUNDS,
    intent=SupportIntent.REFUND_REQUEST,
    language="es",
    urgency="normal",
    summary="Pide el reembolso.",
    order_id=ORDER,
)


@pytest.fixture(scope="module")
def keys() -> RSAKeyPair:
    return RSAKeyPair.generate()


def _google_token(
    keys: RSAKeyPair,
    *,
    audience: str = REFUNDS_AUDIENCE,
    email: str | None = ORCHESTRATOR,
    verified: bool = True,
    issuer: str = "https://accounts.google.com",
) -> str:
    claims: dict[str, Any] = {"email_verified": verified, "azp": "112233445566778899"}
    if email is not None:
        claims["email"] = email
    return keys.create_token(
        subject="112233445566778899",  # Id numérico de la cuenta de servicio.
        issuer=issuer,
        audience=audience,
        additional_claims=claims,
    )


def _verifier(keys: RSAKeyPair) -> JWTVerifier:
    # Igual que `build_verifier` con identity=gcp, con la clave local en lugar del JWKS.
    return JWTVerifier(public_key=keys.public_key, issuer=GOOGLE_ISSUERS, audience=REFUNDS_AUDIENCE)


def test_gcp_verifier_trusts_only_google_and_the_specialist_audience() -> None:
    verifier = build_verifier(GCP, "refunds")

    assert verifier.jwks_uri == GOOGLE_JWKS_URI
    assert verifier.issuer == GOOGLE_ISSUERS
    assert verifier.audience == REFUNDS_AUDIENCE


def test_gcp_policy_uses_the_verified_email_and_no_scope() -> None:
    policy = caller_policy(GCP)

    assert policy == CallerPolicy(frozenset({ORCHESTRATOR}), claim="email", scope=None)
    assert caller_policy(A2ASettings()).scope == "cases:handle"
    with pytest.raises(ValueError, match="emails"):
        caller_policy(A2ASettings(identity="gcp"))  # `svc-orchestrator` no es una cuenta.


async def test_google_id_token_of_the_orchestrator_is_accepted(keys: RSAKeyPair) -> None:
    refund = json.dumps({"order_id": ORDER, "reason": "No puede asistir."})
    world = local_world([[ToolCall("c1", "request_refund", refund)], PENDING])
    agent = build_specialists(world.deps)[Specialist.REFUNDS]
    app = create_specialist_app(
        agent, public_url=URL, verifier=_verifier(keys), policy=caller_policy(GCP)
    )
    token = _google_token(keys)

    async def source() -> str:
        return token

    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url=URL, auth=BearerAuth(source)
    )
    remote = await RemoteSpecialist.connect(Specialist.REFUNDS, URL, http)
    outcome = await remote.handle(REFUND_CASE)

    assert outcome.status is CaseStatus.PENDING_APPROVAL
    await remote.close()


@pytest.mark.parametrize(
    ("claims", "status"),
    [
        ({"audience": "api://genaipocs-agents/creative"}, 401),  # Token de otro especialista.
        ({"issuer": "https://auth.genaipocs.local"}, 401),  # No lo firmó Google.
        ({"email": "otra-cuenta@genaipocs-test.iam.gserviceaccount.com"}, 403),
        ({"email": None}, 403),  # Sin `email` (ID token sin include_email).
        ({"verified": False}, 403),
    ],
)
async def test_invalid_google_tokens_never_reach_the_agent(
    keys: RSAKeyPair, claims: Mapping[str, Any], status: int
) -> None:
    world = local_world()
    agent = build_specialists(world.deps)[Specialist.REFUNDS]
    app = create_specialist_app(
        agent, public_url=URL, verifier=_verifier(keys), policy=caller_policy(GCP)
    )
    token = _google_token(keys, **claims)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=URL) as http:
        response = await http.post(
            "/a2a", json={"jsonrpc": "2.0"}, headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == status
    assert world.llm.calls == []


def _unsigned(exp: float) -> str:
    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part({'exp': exp, 'aud': REFUNDS_AUDIENCE})}.firma"


async def test_id_token_is_cached_until_it_is_about_to_expire() -> None:
    fetched: list[str] = []
    expiries = iter([time.time() + 3600, time.time() + 30, time.time() + 3600])

    def fetch(audience: str) -> str:
        fetched.append(audience)
        return _unsigned(next(expiries))

    source = google_id_token_source(REFUNDS_AUDIENCE, fetch)

    first = await source()
    assert await source() == first  # Vale 1 h: no se vuelve a pedir.
    assert fetched == [REFUNDS_AUDIENCE]
    # Un token que caduca en menos de un minuto se renueva en la siguiente llamada.
    fetched.clear()
    source = google_id_token_source(REFUNDS_AUDIENCE, fetch)
    await source()
    await source()
    assert len(fetched) == 2


def test_orchestrator_picks_google_id_tokens_with_gcp_identity() -> None:
    settings = Settings(a2a=GCP)

    source = a2a_token_source(settings, Specialist.REFUNDS)

    # Sin red: solo se comprueba que no usa el emisor de desarrollo (no crea su clave).
    assert source.__qualname__.startswith("google_id_token_source")

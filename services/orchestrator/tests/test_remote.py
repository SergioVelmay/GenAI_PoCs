"""Especialistas por A2A (fase 9.5): mismo contrato en proceso y en remoto, con OAuth."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace

import httpx
import pytest
from a2a import helpers
from a2a.client import ClientConfig, create_client
from a2a.types import a2a_pb2 as pb
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair
from starlette.applications import Starlette

from headache_core.application.orchestration import (
    InvalidHandoffError,
    SpecialistAgent,
    TriageAgent,
    envelope_to_payload,
)
from headache_core.config import A2ASettings
from headache_core.domain import CaseEnvelope, CaseOutcome, CaseStatus, Specialist, SupportIntent
from headache_core.ports import ToolCall
from headache_mcp.auth import DevTokenIssuer
from orchestrator.graph import RouterGraph
from orchestrator.remote import (
    BearerAuth,
    RemoteSpecialist,
    RemoteSpecialistError,
    build_agent_card,
    create_specialist_app,
    dev_token_source,
)
from orchestrator.specialists import build_approval_service, build_specialists
from orchestrator.testing import ORDER, local_world, support_email

PENDING = "Tu solicitud queda pendiente de revisión por un agente de Headache."
SETTINGS = A2ASettings()
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


def _refund_call() -> list[ToolCall]:
    arguments = json.dumps({"order_id": ORDER, "reason": "No puede asistir."})
    return [ToolCall("c1", "request_refund", arguments)]


def _url(specialist: Specialist) -> str:
    return f"http://{specialist.value}.agents"


def _http(
    app: Starlette, specialist: Specialist, auth: httpx.Auth | None = None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url=_url(specialist), auth=auth
    )


async def _remote(
    agent: SpecialistAgent, *, verifier: JWTVerifier | None = None, auth: httpx.Auth | None = None
) -> RemoteSpecialist:
    app = create_specialist_app(agent, public_url=_url(agent.specialist), verifier=verifier)
    return await RemoteSpecialist.connect(
        agent.specialist, _url(agent.specialist), _http(app, agent.specialist, auth)
    )


@pytest.fixture(scope="module")
def keys() -> RSAKeyPair:
    return RSAKeyPair.generate()


def _verifier(keys: RSAKeyPair, specialist: Specialist) -> JWTVerifier:
    return JWTVerifier(
        public_key=keys.public_key,
        issuer=SETTINGS.issuer,
        audience=SETTINGS.audience(specialist.value),
        algorithm="RS256",
    )


# --- Contrato -------------------------------------------------------------------------------


def test_agent_card_describes_the_specialist_without_exposing_its_tools() -> None:
    card = build_agent_card(Specialist.KNOWLEDGE, "http://knowledge.agents")

    assert card.name == "headache-knowledge-specialist"
    assert card.supported_interfaces[0].url == "http://knowledge.agents/a2a"
    assert card.supported_interfaces[0].protocol_version == "1.0"
    assert sorted(card.skills[0].tags) == ["date_change", "event_info", "lost_tickets"]
    assert card.security_schemes["bearer"].http_auth_security_scheme.bearer_format == "JWT"
    assert "request_refund" not in str(card)


async def test_remote_specialist_gives_the_same_outcome_as_in_process() -> None:
    script: list[str | list[ToolCall] | Exception] = [_refund_call(), PENDING]
    local, remote_world = local_world(script), local_world(script)
    expected = await build_specialists(local.deps)[Specialist.REFUNDS].handle(REFUND_CASE)

    remote = await _remote(build_specialists(remote_world.deps)[Specialist.REFUNDS])
    outcome = await remote.handle(REFUND_CASE)

    assert outcome.status is CaseStatus.PENDING_APPROVAL
    assert replace(outcome, approval_id="x") == replace(expected, approval_id="x")


async def test_invalid_handoff_is_rejected_by_the_remote_agent() -> None:
    agent = build_specialists(local_world().deps)[Specialist.KNOWLEDGE]
    app = create_specialist_app(agent, public_url=_url(Specialist.KNOWLEDGE), verifier=None)
    card = build_agent_card(Specialist.KNOWLEDGE, _url(Specialist.KNOWLEDGE))
    client = await create_client(
        card, ClientConfig(httpx_client=_http(app, Specialist.KNOWLEDGE), streaming=False)
    )
    # Ruta manipulada: un reembolso enviado a Conocimiento (el cliente legítimo lo impediría).
    payload = envelope_to_payload(REFUND_CASE) | {"specialist": "knowledge"}
    message = helpers.new_data_message(payload, role=pb.Role.ROLE_USER)
    message.message_id = str(uuid.uuid4())

    states = [
        r.task.status.state
        async for r in client.send_message(pb.SendMessageRequest(message=message))
        if r.HasField("task")
    ]

    assert states == [pb.TaskState.TASK_STATE_REJECTED]


async def test_client_rejects_an_outcome_for_another_case() -> None:
    class Liar:
        specialist = Specialist.REFUNDS

        async def handle(self, envelope: CaseEnvelope) -> CaseOutcome:
            return CaseOutcome("case_otro", Specialist.REFUNDS, CaseStatus.RESOLVED, "Hecho.")

    remote = await _remote(Liar())

    with pytest.raises(RemoteSpecialistError, match="no corresponde"):
        await remote.handle(REFUND_CASE)


async def test_client_refuses_an_agent_card_of_another_specialist() -> None:
    agent = build_specialists(local_world().deps)[Specialist.CREATIVE]
    app = create_specialist_app(agent, public_url=_url(Specialist.REFUNDS), verifier=None)

    with pytest.raises(RemoteSpecialistError, match="no es el especialista"):
        await RemoteSpecialist.connect(
            Specialist.REFUNDS, _url(Specialist.REFUNDS), _http(app, Specialist.REFUNDS)
        )


# --- OAuth entre agentes --------------------------------------------------------------------


def _issuer(keys: RSAKeyPair) -> DevTokenIssuer:
    return DevTokenIssuer(keys, issuer=SETTINGS.issuer)


async def test_valid_service_token_is_accepted(keys: RSAKeyPair) -> None:
    world = local_world([_refund_call(), PENDING])
    agent = build_specialists(world.deps)[Specialist.REFUNDS]
    source = dev_token_source(_issuer(keys), SETTINGS, "refunds")

    remote = await _remote(
        agent, verifier=_verifier(keys, Specialist.REFUNDS), auth=BearerAuth(source)
    )

    assert (await remote.handle(REFUND_CASE)).status is CaseStatus.PENDING_APPROVAL


@pytest.mark.parametrize(
    ("audience", "subject", "scopes", "status"),
    [
        (None, None, None, 401),  # Sin token.
        ("api://headache-agents/creative", "svc-orchestrator", ["cases:handle"], 401),
        ("api://headache-agents/refunds", "svc-otro", ["cases:handle"], 403),
        ("api://headache-agents/refunds", "svc-orchestrator", ["orders:read"], 403),
    ],
)
async def test_invalid_tokens_never_reach_the_agent(
    keys: RSAKeyPair,
    audience: str | None,
    subject: str | None,
    scopes: list[str] | None,
    status: int,
) -> None:
    world = local_world()
    agent = build_specialists(world.deps)[Specialist.REFUNDS]
    app = create_specialist_app(
        agent, public_url=_url(Specialist.REFUNDS), verifier=_verifier(keys, Specialist.REFUNDS)
    )
    headers: Mapping[str, str] = {}
    if audience and subject and scopes is not None:
        token = _issuer(keys).mint(subject=subject, audience=audience, scopes=scopes)
        headers = {"Authorization": f"Bearer {token}"}

    async with _http(app, Specialist.REFUNDS) as http:
        card = await http.get("/.well-known/agent-card.json")
        response = await http.post("/a2a", json={"jsonrpc": "2.0"}, headers=headers)

    assert card.status_code == 200  # El descubrimiento es público.
    assert response.status_code == status
    assert world.llm.calls == []


# --- Orquestador con especialistas remotos -------------------------------------------------


@pytest.fixture
async def remote_router() -> AsyncIterator[tuple[RouterGraph, list[RemoteSpecialist]]]:
    triage = {
        "intent": "refund_request",
        "language": "es",
        "order_id": ORDER,
        "urgency": "normal",
        "summary": "Pide el reembolso.",
    }
    world = local_world([json.dumps(triage), _refund_call(), PENDING])
    remotes = [await _remote(agent) for agent in build_specialists(world.deps).values()]
    router = RouterGraph(
        TriageAgent(world.llm),
        {remote.specialist: remote for remote in remotes},
        build_approval_service(world.deps),
    )
    yield router, remotes
    for remote in remotes:
        await remote.close()


async def test_orchestrator_works_unchanged_with_remote_specialists(
    remote_router: tuple[RouterGraph, list[RemoteSpecialist]],
) -> None:
    router, _ = remote_router

    run = await router.start(support_email("Devolvedme el dinero."))
    reviewed = await router.review(run["case_id"], reviewer="ops-1", approve=True)

    assert run["awaiting_review"] is True
    assert reviewed["outcome"] is not None
    assert reviewed["outcome"].status is CaseStatus.RESOLVED


async def test_remote_client_validates_the_route_before_sending() -> None:
    remote = await _remote(build_specialists(local_world().deps)[Specialist.KNOWLEDGE])

    with pytest.raises(InvalidHandoffError):
        await remote.handle(REFUND_CASE)

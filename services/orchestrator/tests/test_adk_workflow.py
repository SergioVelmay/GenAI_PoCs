"""Orquestador con un workflow de grafo de ADK: cuarto motor (fase 9.G1).

Mismos casos que LangGraph (`test_graph.py`) y MAF (`test_maf_workflow.py`), más la paridad con
los dos y la reanudación desde otra instancia que comparte el servicio de sesiones.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import RetryConfig

from booking_agent.flavors import FLAVORS, MANAGED_FLAVORS
from genaipocs_core.application.orchestration import TriageAgent
from genaipocs_core.domain import CaseStatus, Specialist, SupportEmail
from genaipocs_core.ports import LLMError, LLMTransientError, ToolCall
from genaipocs_core.ports.approvals import ApprovalStatus
from orchestrator.adk_workflow import (
    APP_NAME,
    PENDING_REVIEW,
    USER_ID,
    AdkCaseWorkflow,
    ReviewError,
    review_answer,
)
from orchestrator.graph import RouterGraph
from orchestrator.maf_workflow import MafCaseWorkflow
from orchestrator.specialists import build_approval_service, build_specialists
from orchestrator.testing import CUSTOMER, ORDER, LocalWorld, local_world, support_email

ALL_FLAVORS = pytest.mark.parametrize("flavor", sorted(set(FLAVORS) - MANAGED_FLAVORS))
NO_WAIT = RetryConfig(max_attempts=2, initial_delay=0, jitter=0, exceptions=[LLMTransientError])
PENDING = "Tu solicitud queda pendiente de revisión por un agente de Genaipocs."
REFUND_EMAIL = support_email("No puedo ir, devolvedme el dinero del pedido.")
INFO_EMAIL = support_email("¿El evento es accesible en silla de ruedas?", order_id=None)
COMPLAINT_EMAIL = support_email("El sonido era malísimo y empezó 40 minutos tarde.")
COMPLAINT_REPLY = json.dumps(
    {
        "subject": "Tu queja sobre el evento",
        "body": "Sentimos lo ocurrido. Hemos trasladado tu queja al organizador y nuestro "
        "equipo la revisará para responderte.",
    }
)


def _triage(intent: str, order_id: str | None = ORDER) -> str:
    return json.dumps(
        {
            "intent": intent,
            "language": "es",
            "order_id": order_id,
            "urgency": "normal",
            "summary": f"Caso de tipo {intent}.",
        }
    )


def _refund_call() -> list[ToolCall]:
    arguments = json.dumps({"order_id": ORDER, "reason": "No puede asistir."})
    return [ToolCall("c1", "request_refund", arguments)]


def _adk(world: LocalWorld, sessions: InMemorySessionService | None = None) -> AdkCaseWorkflow:
    return AdkCaseWorkflow(
        TriageAgent(world.llm),
        build_specialists(world.deps),
        build_approval_service(world.deps),
        session_service=sessions,
        retry_config=NO_WAIT,
    )


async def test_refund_case_pauses_and_another_instance_resumes_it() -> None:
    world = local_world([_triage("refund_request"), _refund_call(), PENDING])
    sessions = InMemorySessionService()

    run = await _adk(world, sessions).start(REFUND_EMAIL)

    assert run["case_id"] == "case_eml_1"
    assert run["awaiting_review"] is True
    assert run["outcome"] is not None
    assert run["outcome"].status is CaseStatus.PENDING_APPROVAL
    assert run["triage_calls"] == 1
    calls = len(world.llm.calls)

    # Otra réplica: workflow y runner nuevos; solo comparten el servicio de sesiones.
    reviewed = await _adk(world, sessions).review("case_eml_1", reviewer="ops-1", approve=True)

    assert reviewed["awaiting_review"] is False
    assert reviewed["outcome"] is not None
    assert reviewed["outcome"].status is CaseStatus.RESOLVED
    assert "approval_executed" in reviewed["outcome"].guardrails
    assert len(world.llm.calls) == calls  # Revisar no llama al modelo.
    [request] = await world.approvals.list_requests()
    assert request.status is ApprovalStatus.EXECUTED


@ALL_FLAVORS
async def test_information_case_is_resolved_in_every_flavor(flavor: str) -> None:
    answer = "Sí, el recinto es accesible; lo indica la ficha del evento."
    world = local_world([_triage("event_info", None), answer], flavor=flavor)

    run = await _adk(world).start(INFO_EMAIL)

    assert run["awaiting_review"] is False
    assert run["outcome"] is not None
    assert run["outcome"].specialist is Specialist.KNOWLEDGE
    assert run["outcome"].status is CaseStatus.RESOLVED
    assert run["outcome"].reply == answer


async def test_rejected_reply_is_escalated_and_never_sent() -> None:
    world = local_world([_triage("complaint"), COMPLAINT_REPLY])
    adk = _adk(world)

    run = await adk.start(COMPLAINT_EMAIL)
    assert run["outcome"] is not None and run["outcome"].specialist is Specialist.CREATIVE
    reviewed = await adk.review("case_eml_1", reviewer="ops-1", approve=False, note="Tono")

    assert reviewed["outcome"] is not None
    assert reviewed["outcome"].status is CaseStatus.ESCALATED
    assert "approval_rejected" in reviewed["outcome"].guardrails
    assert world.outbox.sent == []


async def test_duplicate_email_does_not_reprocess_the_case() -> None:
    world = local_world([_triage("event_info", None), "Respuesta."])
    adk = _adk(world)

    first = await adk.start(INFO_EMAIL)
    calls = len(world.llm.calls)
    second = await adk.start(INFO_EMAIL)

    assert second == first
    assert len(world.llm.calls) == calls


async def test_duplicate_email_of_a_paused_case_keeps_it_paused() -> None:
    world = local_world([_triage("refund_request"), _refund_call(), PENDING])
    adk = _adk(world)

    first = await adk.start(REFUND_EMAIL)
    second = await adk.start(REFUND_EMAIL)

    assert second == first and second["awaiting_review"] is True
    assert len(await world.approvals.list_requests()) == 1


async def test_transient_error_is_retried_inside_the_node() -> None:
    world = local_world([_triage("event_info", None), LLMTransientError("503"), "Respuesta."])

    run = await _adk(world).start(INFO_EMAIL)

    assert run["outcome"] is not None and run["outcome"].status is CaseStatus.RESOLVED


async def test_after_a_crash_the_case_resumes_without_retriaging() -> None:
    world = local_world([_triage("event_info", None), LLMError("credenciales"), "Respuesta."])
    sessions = InMemorySessionService()

    with pytest.raises(LLMError):
        await _adk(world, sessions).start(INFO_EMAIL)
    run = await _adk(world, sessions).start(INFO_EMAIL)

    assert run["outcome"] is not None and run["outcome"].status is CaseStatus.RESOLVED
    triage_calls = [c for c in world.llm.calls if "agente de triaje" in c[0].content]
    assert len(triage_calls) == 1


async def test_invalid_review_keeps_the_case_paused() -> None:
    world = local_world([_triage("refund_request"), _refund_call(), PENDING])
    adk = _adk(world)
    await adk.start(REFUND_EMAIL)

    with pytest.raises(ReviewError):
        await adk.review("case_eml_1", reviewer=CUSTOMER, approve=True)

    still = await adk.get("case_eml_1")
    assert still is not None and still["awaiting_review"] is True
    reviewed = await adk.review("case_eml_1", reviewer="ops-1", approve=True)
    assert reviewed["outcome"] is not None
    assert reviewed["outcome"].status is CaseStatus.RESOLVED


async def test_invalid_resume_sent_to_the_workflow_pauses_again() -> None:
    world = local_world([_triage("refund_request"), _refund_call(), PENDING])
    adk = _adk(world)
    await adk.start(REFUND_EMAIL)
    session = await adk.sessions.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id="case_eml_1"
    )
    assert session is not None
    pending = session.state[PENDING_REVIEW]

    # Saltándose `review()`: el nodo vuelve a validar y pide otra decisión con un id nuevo.
    events = [
        event
        async for event in adk.runner.run_async(
            user_id=USER_ID,
            session_id="case_eml_1",
            new_message=review_answer(pending["interrupt_id"], {"reviewer": CUSTOMER}),
            invocation_id=pending["invocation_id"],
        )
    ]

    [pause] = [e for e in events if e.long_running_tool_ids]
    [call] = pause.get_function_calls()
    assert call.args is not None and "approve" in call.args["payload"]["error"]
    assert pause.long_running_tool_ids == {"review-2"}
    reviewed = await adk.review("case_eml_1", reviewer="ops-1", approve=True)
    assert reviewed["outcome"] is not None
    assert reviewed["outcome"].status is CaseStatus.RESOLVED


async def test_review_of_a_case_that_is_not_waiting_is_rejected() -> None:
    world = local_world([_triage("event_info", None), "Respuesta."])
    adk = _adk(world)
    await adk.start(INFO_EMAIL)

    with pytest.raises(ReviewError):
        await adk.review("case_eml_1", reviewer="ops-1", approve=True)
    with pytest.raises(ReviewError):
        await adk.review("case_inexistente", reviewer="ops-1", approve=True)


async def test_triage_misroutes_cannot_reach_refund_tools() -> None:
    world = local_world([_triage("event_info"), _refund_call(), "No puedo hacerlo."])

    run = await _adk(world).start(REFUND_EMAIL)

    assert run["outcome"] is not None
    assert run["outcome"].specialist is Specialist.KNOWLEDGE
    assert await world.approvals.list_requests() == []


def test_workflow_shape() -> None:
    workflow = _adk(local_world()).workflow
    assert workflow.graph is not None
    names = {node.name for node in workflow.graph.nodes}

    assert {"triage", "refunds", "creative", "knowledge", "review", "unroutable"} <= names


@pytest.mark.parametrize(
    ("email", "script", "specialist", "status"),
    [
        (
            REFUND_EMAIL,
            [_triage("refund_request"), _refund_call(), PENDING],
            Specialist.REFUNDS,
            CaseStatus.PENDING_APPROVAL,
        ),
        (
            INFO_EMAIL,
            [_triage("event_info", None), "Respuesta."],
            Specialist.KNOWLEDGE,
            CaseStatus.RESOLVED,
        ),
        (
            COMPLAINT_EMAIL,
            [_triage("complaint"), COMPLAINT_REPLY],
            Specialist.CREATIVE,
            CaseStatus.PENDING_APPROVAL,
        ),
    ],
)
async def test_same_result_as_langgraph_and_maf(
    email: SupportEmail,
    script: list[str | list[ToolCall]],
    specialist: Specialist,
    status: CaseStatus,
) -> None:
    adk_world, lg_world, maf_world = local_world(script), local_world(script), local_world(script)

    adk = await _adk(adk_world).start(email)
    lg = await RouterGraph(
        TriageAgent(lg_world.llm),
        build_specialists(lg_world.deps),
        build_approval_service(lg_world.deps),
    ).start(email)
    maf = await MafCaseWorkflow(
        TriageAgent(maf_world.llm),
        build_specialists(maf_world.deps),
        build_approval_service(maf_world.deps),
    ).start(email)

    assert adk["outcome"] is not None and lg["outcome"] is not None and maf.outcome is not None
    assert (adk["outcome"].specialist, adk["outcome"].status) == (specialist, status)
    # Todo igual salvo el id aleatorio de la solicitud de aprobación.
    assert replace(adk["outcome"], approval_id="x") == replace(lg["outcome"], approval_id="x")
    assert replace(adk["outcome"], approval_id="x") == replace(maf.outcome, approval_id="x")
    assert len(adk_world.llm.calls) == len(lg_world.llm.calls) == len(maf_world.llm.calls)

"""Orquestador LangGraph: router determinista, subgrafos, checkpoints e `interrupt` (fase 9.3)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langgraph.types import Command, RetryPolicy

from booking_agent.flavors import FLAVORS, MANAGED_FLAVORS
from headache_core.adapters.otel import recording_telemetry
from headache_core.application.orchestration import TriageAgent
from headache_core.application.telemetry import metrics
from headache_core.domain import CaseStatus, Specialist
from headache_core.ports import LLMError, LLMTransientError, ToolCall
from headache_core.ports.approvals import ApprovalStatus
from orchestrator.graph import ReviewError, RouterGraph
from orchestrator.specialists import build_approval_service, build_specialists
from orchestrator.supervisor import SupervisorGraph
from orchestrator.testing import CUSTOMER, ORDER, LocalWorld, local_world, support_email

ALL_FLAVORS = pytest.mark.parametrize("flavor", sorted(set(FLAVORS) - MANAGED_FLAVORS))
NO_WAIT = RetryPolicy(initial_interval=0, jitter=False, max_attempts=2, retry_on=LLMTransientError)
PENDING = "Tu solicitud queda pendiente de revisión por un agente de Headache."
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


def _router(world: LocalWorld) -> RouterGraph:
    return RouterGraph(
        TriageAgent(world.llm),
        build_specialists(world.deps),
        build_approval_service(world.deps),
        retry_policy=NO_WAIT,
    )


async def test_refund_case_pauses_for_a_human_and_resumes_without_the_model() -> None:
    world = local_world([_triage("refund_request"), _refund_call(), PENDING])
    router = _router(world)

    run = await router.start(REFUND_EMAIL)

    assert run["case_id"] == "case_eml_1"
    assert run["awaiting_review"] is True
    assert run["outcome"] is not None
    assert run["outcome"].status is CaseStatus.PENDING_APPROVAL
    assert run["triage_calls"] == 1
    calls = len(world.llm.calls)

    reviewed = await router.review("case_eml_1", reviewer="ops-1", approve=True)

    assert reviewed["awaiting_review"] is False
    assert reviewed["outcome"] is not None
    assert reviewed["outcome"].status is CaseStatus.RESOLVED
    assert "approval_executed" in reviewed["outcome"].guardrails
    assert len(world.llm.calls) == calls  # Revisar no llama al modelo.
    [request] = await world.approvals.list_requests()
    assert request.status is ApprovalStatus.EXECUTED


@ALL_FLAVORS
async def test_information_case_is_resolved_by_knowledge_in_every_flavor(flavor: str) -> None:
    answer = "Sí, el recinto es accesible; lo indica la ficha del evento."
    world = local_world([_triage("event_info", None), answer], flavor=flavor)

    run = await _router(world).start(INFO_EMAIL)

    assert run["awaiting_review"] is False
    assert run["outcome"] is not None
    assert run["outcome"].specialist is Specialist.KNOWLEDGE
    assert run["outcome"].status is CaseStatus.RESOLVED
    assert run["outcome"].reply == answer


async def test_triage_counts_intents_for_drift_detection() -> None:
    world = local_world([_triage("event_info", None)])
    with recording_telemetry() as recorded:
        await TriageAgent(world.llm).triage(INFO_EMAIL)
    intent = {"headache.case.intent": "event_info", "headache.case.language": "es"}
    assert recorded.total(metrics.CASES_TRIAGED, **intent) == 1


async def test_rejected_reply_is_escalated_and_never_sent() -> None:
    world = local_world([_triage("complaint"), COMPLAINT_REPLY])
    router = _router(world)

    run = await router.start(COMPLAINT_EMAIL)
    assert run["outcome"] is not None and run["outcome"].specialist is Specialist.CREATIVE
    reviewed = await router.review("case_eml_1", reviewer="ops-1", approve=False, note="Tono")

    assert reviewed["outcome"] is not None
    assert reviewed["outcome"].status is CaseStatus.ESCALATED
    assert "approval_rejected" in reviewed["outcome"].guardrails
    assert world.outbox.sent == []


async def test_duplicate_email_does_not_reprocess_the_case() -> None:
    world = local_world([_triage("event_info", None), "Respuesta."])
    router = _router(world)

    first = await router.start(INFO_EMAIL)
    calls = len(world.llm.calls)
    second = await router.start(INFO_EMAIL)

    assert second == first
    assert len(world.llm.calls) == calls


async def test_transient_error_is_retried_inside_the_node() -> None:
    world = local_world([_triage("event_info", None), LLMTransientError("503"), "Respuesta."])

    run = await _router(world).start(INFO_EMAIL)

    assert run["outcome"] is not None and run["outcome"].status is CaseStatus.RESOLVED


async def test_after_a_crash_the_case_resumes_from_the_checkpoint_without_retriaging() -> None:
    world = local_world([_triage("event_info", None), LLMError("credenciales"), "Respuesta."])
    router = _router(world)

    with pytest.raises(LLMError):
        await router.start(INFO_EMAIL)
    run = await router.start(INFO_EMAIL)

    assert run["outcome"] is not None and run["outcome"].status is CaseStatus.RESOLVED
    triage_calls = [c for c in world.llm.calls if "agente de triaje" in c[0].content]
    assert len(triage_calls) == 1


async def test_invalid_review_keeps_the_case_paused() -> None:
    world = local_world([_triage("refund_request"), _refund_call(), PENDING])
    router = _router(world)
    await router.start(REFUND_EMAIL)

    with pytest.raises(ReviewError):
        await router.review("case_eml_1", reviewer=CUSTOMER, approve=True)

    still = await router.get("case_eml_1")
    assert still is not None and still["awaiting_review"] is True
    reviewed = await router.review("case_eml_1", reviewer="ops-1", approve=True)
    assert reviewed["outcome"] is not None
    assert reviewed["outcome"].status is CaseStatus.RESOLVED


async def test_invalid_resume_sent_to_the_graph_pauses_again() -> None:
    world = local_world([_triage("refund_request"), _refund_call(), PENDING])
    router = _router(world)
    await router.start(REFUND_EMAIL)
    config: Any = {"configurable": {"thread_id": "case_eml_1"}}

    result = await router.graph.ainvoke(Command(resume={"reviewer": CUSTOMER}), config)

    [pause] = result["__interrupt__"]
    assert "approve" in pause.value["error"]
    reviewed = await router.review("case_eml_1", reviewer="ops-1", approve=True)
    assert reviewed["outcome"] is not None
    assert reviewed["outcome"].status is CaseStatus.RESOLVED


async def test_review_of_a_case_that_is_not_waiting_is_rejected() -> None:
    world = local_world([_triage("event_info", None), "Respuesta."])
    router = _router(world)
    await router.start(INFO_EMAIL)

    with pytest.raises(ReviewError):
        await router.review("case_eml_1", reviewer="ops-1", approve=True)


async def test_triage_misroutes_cannot_reach_refund_tools() -> None:
    # Prompt injection que cuela la intención event_info en un email de reembolso: el caso va a
    # Conocimiento, que no tiene request_refund; el modelo lo intenta y la tool no existe.
    world = local_world([_triage("event_info"), _refund_call(), "No puedo hacerlo."])

    run = await _router(world).start(REFUND_EMAIL)

    assert run["outcome"] is not None
    assert run["outcome"].specialist is Specialist.KNOWLEDGE
    assert await world.approvals.list_requests() == []


def test_graph_shape() -> None:
    mermaid = _router(local_world()).graph.get_graph().draw_mermaid()

    for node in ("triage", "refunds", "creative", "knowledge", "human_review"):
        assert node in mermaid


# --- Supervisor (comparativa) --------------------------------------------------------------


def _step(next_step: str) -> str:
    return json.dumps({"next": next_step, "urgency": "normal", "summary": f"Atender {next_step}."})


async def test_supervisor_handles_several_intents_in_one_email() -> None:
    world = local_world(
        [
            _step("refund_request"),
            _refund_call(),
            PENDING,
            _step("complaint"),
            COMPLAINT_REPLY,
            _step("finish"),
        ]
    )
    supervisor = SupervisorGraph(world.llm, build_specialists(world.deps))
    email = support_email("Devolvedme el dinero y además quiero quejarme: el sonido fue horrible.")

    run = await supervisor.run(email)

    assert [o.specialist for o in run["outcomes"]] == [Specialist.REFUNDS, Specialist.CREATIVE]
    assert all(o.status is CaseStatus.PENDING_APPROVAL for o in run["outcomes"])
    assert run["supervisor_calls"] == 3
    assert run["stop"] == "finish"
    assert len(await world.approvals.list_requests()) == 2


async def test_supervisor_loop_is_cut_by_the_domain() -> None:
    world = local_world([_step("event_info"), "Respuesta.", _step("event_info")])
    supervisor = SupervisorGraph(world.llm, build_specialists(world.deps))

    run = await supervisor.run(INFO_EMAIL)

    assert len(run["outcomes"]) == 1
    assert run["stop"] == "repeated"


async def test_supervisor_respects_the_step_budget() -> None:
    world = local_world([_step("event_info"), "Respuesta."])
    supervisor = SupervisorGraph(world.llm, build_specialists(world.deps), max_steps=1)

    run = await supervisor.run(INFO_EMAIL)

    assert run["stop"] == "max_steps"
    assert run["supervisor_calls"] == 1

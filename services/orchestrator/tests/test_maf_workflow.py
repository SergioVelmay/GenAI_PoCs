"""El mismo flujo en MAF (fase 9.4): mismos especialistas, mismas garantías que LangGraph."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from agent_framework import CheckpointStorage, FileCheckpointStorage

from booking_agent.flavors import FLAVORS, MANAGED_FLAVORS
from headache_core.application.orchestration import TriageAgent
from headache_core.domain import CaseStatus, Specialist, SupportEmail
from headache_core.ports import ToolCall
from headache_core.ports.approvals import ApprovalStatus
from orchestrator.graph import RouterGraph
from orchestrator.maf_workflow import (
    CheckpointNotSavedError,
    MafCaseWorkflow,
    file_checkpoint_storage,
)
from orchestrator.review import ReviewError
from orchestrator.specialists import build_approval_service, build_specialists
from orchestrator.testing import CUSTOMER, ORDER, LocalWorld, local_world, support_email

ALL_FLAVORS = pytest.mark.parametrize("flavor", sorted(set(FLAVORS) - MANAGED_FLAVORS))
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


def _maf(world: LocalWorld, storage: CheckpointStorage | None = None) -> MafCaseWorkflow:
    return MafCaseWorkflow(
        TriageAgent(world.llm),
        build_specialists(world.deps),
        build_approval_service(world.deps),
        checkpoint_storage=storage,
    )


async def test_refund_case_pauses_and_another_instance_resumes_it(tmp_path: Path) -> None:
    world = local_world([_triage("refund_request"), _refund_call(), PENDING])
    storage = file_checkpoint_storage(tmp_path)

    run = await _maf(world, storage).start(REFUND_EMAIL)

    assert run.case_id == "case_eml_1"
    assert run.awaiting_review
    assert run.outcome is not None and run.outcome.status is CaseStatus.PENDING_APPROVAL
    calls = len(world.llm.calls)

    # "Reinicio": otra instancia del orquestador con el mismo almacén de checkpoints en disco.
    reviewed = await _maf(world, file_checkpoint_storage(tmp_path)).review(
        run, reviewer="ops-1", approve=True
    )

    assert not reviewed.awaiting_review
    assert reviewed.outcome is not None and reviewed.outcome.status is CaseStatus.RESOLVED
    assert "approval_executed" in reviewed.outcome.guardrails
    assert len(world.llm.calls) == calls
    [request] = await world.approvals.list_requests()
    assert request.status is ApprovalStatus.EXECUTED


async def test_a_pause_without_checkpoint_fails_loudly(tmp_path: Path) -> None:
    # Sin la lista de tipos permitidos, MAF no puede guardar el checkpoint y solo avisa en el log:
    # el caso quedaría en pausa sin forma de reanudarlo desde otra réplica.
    world = local_world([_triage("refund_request"), _refund_call(), PENDING])

    with pytest.raises(CheckpointNotSavedError):
        await _maf(world, FileCheckpointStorage(tmp_path)).start(REFUND_EMAIL)


@ALL_FLAVORS
async def test_information_case_is_resolved_in_every_flavor(flavor: str) -> None:
    answer = "Sí, el recinto es accesible; lo indica la ficha del evento."
    world = local_world([_triage("event_info", None), answer], flavor=flavor)

    run = await _maf(world).start(INFO_EMAIL)

    assert not run.awaiting_review
    assert run.outcome is not None
    assert run.outcome.specialist is Specialist.KNOWLEDGE
    assert run.outcome.status is CaseStatus.RESOLVED
    assert run.outcome.reply == answer


async def test_rejected_reply_is_escalated_and_never_sent() -> None:
    world = local_world([_triage("complaint"), COMPLAINT_REPLY])
    workflow = _maf(world)

    run = await workflow.start(COMPLAINT_EMAIL)
    reviewed = await workflow.review(run, reviewer="ops-1", approve=False)

    assert reviewed.outcome is not None
    assert reviewed.outcome.status is CaseStatus.ESCALATED
    assert "approval_rejected" in reviewed.outcome.guardrails
    assert world.outbox.sent == []


async def test_invalid_review_pauses_the_case_again() -> None:
    world = local_world([_triage("refund_request"), _refund_call(), PENDING])
    workflow = _maf(world)
    run = await workflow.start(REFUND_EMAIL)

    again = await workflow.review(run, reviewer=CUSTOMER, approve=True)

    assert again.awaiting_review
    assert "distinta" in again.error
    reviewed = await workflow.review(again, reviewer="ops-1", approve=True)
    assert reviewed.outcome is not None
    assert reviewed.outcome.status is CaseStatus.RESOLVED


async def test_review_of_a_case_that_is_not_waiting_is_rejected() -> None:
    world = local_world([_triage("event_info", None), "Respuesta."])
    workflow = _maf(world)
    run = await workflow.start(INFO_EMAIL)

    with pytest.raises(ReviewError):
        await workflow.review(run, reviewer="ops-1", approve=True)


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
async def test_same_result_as_langgraph(
    email: SupportEmail,
    script: list[str | list[ToolCall]],
    specialist: Specialist,
    status: CaseStatus,
) -> None:
    maf_world, lg_world = local_world(script), local_world(script)

    maf = await _maf(maf_world).start(email)
    lg = await RouterGraph(
        TriageAgent(lg_world.llm),
        build_specialists(lg_world.deps),
        build_approval_service(lg_world.deps),
    ).start(email)

    assert maf.outcome is not None and lg["outcome"] is not None
    assert (maf.outcome.specialist, maf.outcome.status) == (specialist, status)
    # Todo igual salvo el id aleatorio de la solicitud de aprobación.
    assert replace(maf.outcome, approval_id="x") == replace(lg["outcome"], approval_id="x")
    assert len(maf_world.llm.calls) == len(lg_world.llm.calls)

"""Revisión humana de un caso pendiente: la misma lógica para LangGraph y MAF.

La decisión de la persona autoriza; `ApprovalService` ejecuta exactamente lo congelado y el
estado final del caso sale del resultado de esa ejecución, no de lo que diga ningún modelo.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from harness.approvals import ApprovalError, ApprovalService
from headache_core.domain import CaseOutcome, CaseStatus
from headache_core.ports.approvals import ApprovalStatus


class ReviewError(ValueError):
    """La decisión del revisor está mal formada: el caso sigue en pausa."""


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    reviewer: str
    approve: bool
    note: str = ""


def parse_decision(decision: object, customer_id: str) -> ReviewDecision:
    reviewer: object
    approve: object
    if isinstance(decision, ReviewDecision):
        reviewer, approve, note = decision.reviewer, decision.approve, decision.note
    elif isinstance(decision, Mapping):
        reviewer, approve = decision.get("reviewer"), decision.get("approve")
        note = str(decision.get("note", ""))
    else:
        raise ReviewError("La decisión debe ser un objeto {reviewer, approve, note}.")
    if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(approve, bool):
        raise ReviewError("La decisión necesita reviewer (texto) y approve (booleano).")
    if reviewer == customer_id:
        raise ReviewError("El revisor debe ser una persona distinta del cliente.")
    return ReviewDecision(reviewer, approve, note)


async def apply_review(
    approvals: ApprovalService, outcome: CaseOutcome, decision: ReviewDecision
) -> CaseOutcome:
    assert outcome.approval_id is not None
    try:
        decided = await approvals.decide(
            outcome.approval_id,
            reviewer=decision.reviewer,
            approve=decision.approve,
            note=decision.note,
        )
    except ApprovalError:
        # Caducada o decidida por otra vía: el caso pasa a una persona.
        return _reviewed(outcome, CaseStatus.ESCALATED, "approval_error")
    executed = decided.status is ApprovalStatus.EXECUTED
    status = CaseStatus.RESOLVED if executed else CaseStatus.ESCALATED
    return _reviewed(outcome, status, f"approval_{decided.status.value}")


def _reviewed(outcome: CaseOutcome, status: CaseStatus, code: str) -> CaseOutcome:
    return CaseOutcome(
        case_id=outcome.case_id,
        specialist=outcome.specialist,
        status=status,
        reply=outcome.reply,
        guardrails=(*outcome.guardrails, code),
        approval_id=outcome.approval_id,
    )

"""Especialista con tools: un `AgentRuntime` (cualquier sabor) detrás del contrato común.

Cada caso es un hilo nuevo (`thread_id = case_id`) y un agente nuevo: sin estado compartido
entre casos (el diario del ejecutor no crece en un worker de larga duración) y el mismo
comportamiento en proceso, en un worker de cola o detrás de A2A.
"""

from __future__ import annotations

from collections.abc import Callable

from headache_core.application.agents.runtime import AgentRuntime, AgentTurn
from headache_core.application.orchestration import (
    InvalidHandoffError,
    render_handoff,
    tool_context,
)
from headache_core.domain import CaseEnvelope, CaseOutcome, CaseStatus, Specialist


class AgentSpecialist:
    def __init__(self, specialist: Specialist, runtime: Callable[[], AgentRuntime]) -> None:
        self._specialist = specialist
        self._runtime = runtime

    @property
    def specialist(self) -> Specialist:
        return self._specialist

    async def handle(self, envelope: CaseEnvelope) -> CaseOutcome:
        if envelope.specialist is not self._specialist:
            raise InvalidHandoffError(
                f"Caso para {envelope.specialist}, no para {self._specialist}."
            )
        agent = self._runtime()
        turn = await agent.respond(
            envelope.case_id, render_handoff(envelope), tool_context(envelope)
        )
        return outcome_from_turn(envelope, self._specialist, turn)


def outcome_from_turn(
    envelope: CaseEnvelope, specialist: Specialist, turn: AgentTurn
) -> CaseOutcome:
    """El estado del caso lo decide el código a partir del turno, no lo que cuente el modelo."""
    if turn.stop_reason != "answer":
        # Bloqueo del harness o límite de iteraciones: lo revisa una persona.
        status, approval_id = CaseStatus.ESCALATED, None
    elif turn.approvals:
        status, approval_id = CaseStatus.PENDING_APPROVAL, turn.approvals[0]
    else:
        status, approval_id = CaseStatus.RESOLVED, None
    return CaseOutcome(
        case_id=envelope.case_id,
        specialist=specialist,
        status=status,
        reply=turn.answer,
        guardrails=turn.guardrails,
        approval_id=approval_id,
    )

"""Orquestador con LangGraph (carril C): patrón *router* con revisión humana.

    START → triage ─┬─▶ refunds ───┐
                    ├─▶ creative ──┼─▶ (pendiente de aprobación?) ─▶ human_review ─▶ END
                    └─▶ knowledge ─┘                 └─ no ───────────────────────▶ END

- Arista condicional: la elige el DOMINIO (`envelope.specialist`, de `ROUTES`), no un LLM.
- Subgrafos: cada especialista es un grafo propio (`SpecialistState`) que comparte con el padre
  solo las claves `envelope` y `outcome`: el padre no ve los detalles internos.
- Checkpoints: el estado se guarda tras cada paso por `thread_id = case_id` (en memoria en los
  tests; `AsyncPostgresSaver` en producción). Un fallo después del triaje no repite el triaje.
- `interrupt`: el caso pendiente se PAUSA (sin hilo ni proceso esperando) hasta que una persona
  decide; `Command(resume=...)` lo reanuda horas después, en otra réplica si hace falta.
- Estado en JSON plano: se serializa igual en memoria, en PostgreSQL o en una cola.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, RetryPolicy, interrupt

from harness.approvals import ApprovalService
from headache_core.application.orchestration import (
    SpecialistAgent,
    TriageAgent,
    case_id_for,
    email_from_payload,
    email_to_payload,
    envelope_from_payload,
    envelope_to_payload,
    outcome_from_payload,
    outcome_to_payload,
)
from headache_core.domain import CaseOutcome, CaseStatus, Specialist, SupportEmail
from headache_core.ports import LLMTransientError
from orchestrator.review import ReviewError, apply_review, parse_decision

type Payload = dict[str, Any]

HUMAN_REVIEW = "human_review"
# Solo errores transitorios del modelo: reintentar un error de lógica no lo arregla.
SPECIALIST_RETRY = RetryPolicy(max_attempts=3, retry_on=LLMTransientError)


class CaseState(TypedDict, total=False):
    email: Payload
    envelope: Payload
    outcome: Payload
    review: Payload
    triage_calls: int


class SpecialistState(TypedDict, total=False):
    envelope: Payload
    outcome: Payload


__all__ = ["CaseRun", "CaseState", "ReviewError", "RouterGraph", "specialist_subgraph"]


def specialist_subgraph(
    agent: SpecialistAgent, retry_policy: RetryPolicy = SPECIALIST_RETRY
) -> CompiledStateGraph[SpecialistState, None, SpecialistState, SpecialistState]:
    async def handle(state: SpecialistState) -> SpecialistState:
        outcome = await agent.handle(envelope_from_payload(state["envelope"]))
        return {"outcome": outcome_to_payload(outcome)}

    graph = StateGraph(SpecialistState)
    # Reintentar es seguro: las aprobaciones son idempotentes (misma acción = misma solicitud).
    graph.add_node("handle", handle, retry_policy=retry_policy)
    graph.add_edge(START, "handle")
    graph.add_edge("handle", END)
    return graph.compile(name=f"specialist_{agent.specialist.value}")


class RouterGraph:
    def __init__(
        self,
        triage: TriageAgent,
        specialists: Mapping[Specialist, SpecialistAgent],
        approvals: ApprovalService,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        retry_policy: RetryPolicy = SPECIALIST_RETRY,
    ) -> None:
        missing = set(Specialist) - set(specialists)
        if missing:
            raise ValueError(f"Faltan especialistas: {sorted(missing)}.")
        self._triage = triage
        self._approvals = approvals
        self._retry = retry_policy
        self.graph = self._build(specialists, checkpointer or InMemorySaver())

    def _build(
        self,
        specialists: Mapping[Specialist, SpecialistAgent],
        checkpointer: BaseCheckpointSaver[Any],
    ) -> CompiledStateGraph[CaseState, None, CaseState, CaseState]:
        graph = StateGraph(CaseState)
        graph.add_node("triage", self._triage_node, retry_policy=self._retry)
        for specialist, agent in specialists.items():
            graph.add_node(specialist.value, specialist_subgraph(agent, self._retry))
            graph.add_conditional_edges(specialist.value, _after_specialist, [HUMAN_REVIEW, END])
        graph.add_node(HUMAN_REVIEW, self._human_review)
        graph.add_edge(START, "triage")
        graph.add_conditional_edges("triage", _route, [s.value for s in Specialist])
        graph.add_edge(HUMAN_REVIEW, END)
        return graph.compile(checkpointer=checkpointer, name="headache_case_router")

    async def _triage_node(self, state: CaseState) -> CaseState:
        result = await self._triage.triage(email_from_payload(state["email"]))
        return {
            "envelope": envelope_to_payload(result.envelope),
            "triage_calls": len(result.responses),
        }

    async def _human_review(self, state: CaseState) -> CaseState:
        outcome = outcome_from_payload(state["outcome"])
        assert outcome.approval_id is not None
        pause: dict[str, str] = {
            "case_id": outcome.case_id,
            "approval_id": outcome.approval_id,
            "specialist": outcome.specialist.value,
            "draft": outcome.reply,
        }
        customer_id = state["envelope"]["customer_id"]
        # Todo lo anterior a `interrupt` se re-ejecuta al reanudar: aquí no hay efectos antes.
        # LangGraph guarda cada valor de reanudación: si uno es inválido, lanzar dejaría el caso
        # atascado repitiéndolo. Se vuelve a pausar hasta recibir una decisión válida.
        while True:
            try:
                decision = parse_decision(interrupt(pause), customer_id)
                break
            except ReviewError as exc:
                pause = {**pause, "error": str(exc)}
        reviewed = await apply_review(self._approvals, outcome, decision)
        return {
            "outcome": outcome_to_payload(reviewed),
            "review": {"reviewer": decision.reviewer, "approve": decision.approve},
        }

    # --- API del orquestador -----------------------------------------------------------------

    async def start(self, email: SupportEmail) -> CaseRun:
        """Idempotente por email: un duplicado devuelve el estado del caso, no lo reprocesa."""
        config = _config(case_id_for(email))
        snapshot = await self.graph.aget_state(config)
        if snapshot.values and (snapshot.interrupts or not snapshot.next):
            return _run(snapshot.values, snapshot.interrupts)
        # Sin estado: caso nuevo. Con pasos pendientes: un fallo anterior lo dejó a medias y se
        # reanuda desde el último checkpoint (sin repetir el triaje ni pasos ya hechos).
        graph_input: CaseState | None = (
            None if snapshot.values else {"email": email_to_payload(email)}
        )
        result = await self.graph.ainvoke(graph_input, config)
        return _run(result, result.get("__interrupt__", ()))

    async def review(
        self, case_id: str, *, reviewer: str, approve: bool, note: str = ""
    ) -> CaseRun:
        config = _config(case_id)
        snapshot = await self.graph.aget_state(config)
        if not snapshot.interrupts:
            raise ReviewError(f"El caso {case_id} no espera revisión.")
        resume = {"reviewer": reviewer, "approve": approve, "note": note}
        # Error inmediato para quien llama; el nodo vuelve a validar (defensa en profundidad).
        parse_decision(resume, snapshot.values["envelope"]["customer_id"])
        result = await self.graph.ainvoke(Command(resume=resume), config)
        return _run(result, result.get("__interrupt__", ()))

    async def get(self, case_id: str) -> CaseRun | None:
        snapshot = await self.graph.aget_state(_config(case_id))
        return _run(snapshot.values, snapshot.interrupts) if snapshot.values else None


class CaseRun(TypedDict):
    """Vista del caso para quien llama (API, worker de cola, panel de operaciones)."""

    case_id: str
    outcome: CaseOutcome | None
    awaiting_review: bool
    triage_calls: int


def _config(case_id: str) -> Any:
    return {"configurable": {"thread_id": case_id}}


def _run(values: Mapping[str, Any], interrupts: Any) -> CaseRun:
    email = values["email"]
    outcome = values.get("outcome")
    return {
        "case_id": case_id_for(email_from_payload(email)),
        "outcome": outcome_from_payload(outcome) if outcome else None,
        "awaiting_review": bool(interrupts),
        "triage_calls": int(values.get("triage_calls", 0)),
    }


def _route(state: CaseState) -> str:
    return Specialist(envelope_from_payload(state["envelope"]).specialist).value


def _after_specialist(state: CaseState) -> str:
    status = state["outcome"]["status"]
    return HUMAN_REVIEW if status == CaseStatus.PENDING_APPROVAL else END

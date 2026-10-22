"""Variante *supervisor* con LangGraph, para comparar con el router.

    START → supervisor ⇄ {refunds | creative | knowledge} → … → END

Un LLM decide en bucle qué intención atender a continuación (o terminar) viendo el email y los
resultados anteriores. Gana en emails con varias intenciones ("devolvedme el dinero y además
quiero quejarme"), que el router atiende solo por la principal. Paga una llamada al LLM por paso
y es menos predecible, así que el dominio sigue mandando:
- el supervisor elige una INTENCIÓN; el especialista lo pone `ROUTES`;
- cada intención se atiende una sola vez y hay un máximo de pasos;
- identidad y pedido salen del sistema, como en el triaje.
Los casos que quedan pendientes de aprobación se deciden fuera (`ApprovalService`).
"""

from __future__ import annotations

import operator
from collections.abc import Mapping
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from headache_core.application.llm import complete_structured
from headache_core.application.orchestration import (
    SpecialistAgent,
    case_id_for,
    email_from_payload,
    email_to_payload,
    envelope_from_payload,
    envelope_to_payload,
    outcome_from_payload,
    outcome_to_payload,
)
from headache_core.domain import (
    CaseEnvelope,
    CaseOutcome,
    Specialist,
    SupportEmail,
    SupportIntent,
    resolve_language,
    resolve_order_id,
    route,
)
from headache_core.ports import ChatMessage, LLMPort, Role
from orchestrator.graph import SPECIALIST_RETRY
from orchestrator.prompts import PROMPTS

type Payload = dict[str, Any]

FINISH = "finish"
MAX_STEPS = 3


class SupervisorDecision(BaseModel):
    """Siguiente paso del supervisor de soporte de Headache."""

    model_config = ConfigDict(extra="forbid")

    next: SupportIntent | Literal["finish"] = Field(
        description="Intención pendiente que hay que atender ahora, o finish si no queda ninguna."
    )
    urgency: Literal["low", "normal", "high"]
    summary: str = Field(description="Qué debe resolver el especialista, en una frase, en español")


SUPERVISOR_PROMPT = PROMPTS.get("supervisor").text


class SupervisorState(TypedDict, total=False):
    email: Payload
    envelope: Payload
    handled: Annotated[list[str], operator.add]
    outcomes: Annotated[list[Payload], operator.add]
    supervisor_calls: Annotated[int, operator.add]
    stop: str


class SupervisorGraph:
    def __init__(
        self,
        llm: LLMPort,
        specialists: Mapping[Specialist, SpecialistAgent],
        *,
        max_steps: int = MAX_STEPS,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ) -> None:
        self._llm = llm
        self._specialists = specialists
        self._max_steps = max_steps
        self.graph = self._build(checkpointer or InMemorySaver())

    def _build(
        self, checkpointer: BaseCheckpointSaver[Any]
    ) -> CompiledStateGraph[SupervisorState, None, SupervisorState, SupervisorState]:
        graph = StateGraph(SupervisorState)
        destinations = (*(s.value for s in Specialist), END)
        graph.add_node("supervisor", self._supervisor, destinations=destinations)
        for specialist, agent in self._specialists.items():
            graph.add_node(specialist.value, _specialist_node(agent), retry_policy=SPECIALIST_RETRY)
            graph.add_edge(specialist.value, "supervisor")
        graph.add_edge(START, "supervisor")
        return graph.compile(checkpointer=checkpointer, name="headache_case_supervisor")

    async def _supervisor(self, state: SupervisorState) -> Command[str]:
        handled = state.get("handled", [])
        if len(handled) >= self._max_steps:
            return Command(update={"stop": "max_steps"}, goto=END)
        email = email_from_payload(state["email"])
        result = await complete_structured(
            self._llm,
            [
                ChatMessage(Role.SYSTEM, SUPERVISOR_PROMPT),
                ChatMessage(
                    Role.USER, _supervisor_input(email, handled, state.get("outcomes", []))
                ),
            ],
            SupervisorDecision,
            max_tokens=200,
        )
        decision = result.value
        calls = len(result.responses)
        if decision.next == FINISH:
            return Command(update={"supervisor_calls": calls, "stop": FINISH}, goto=END)
        intent = SupportIntent(decision.next)
        if intent.value in handled:
            # El modelo repite: el dominio corta el bucle en lugar de pagar otra vuelta.
            return Command(update={"supervisor_calls": calls, "stop": "repeated"}, goto=END)
        envelope = CaseEnvelope(
            case_id=f"{case_id_for(email)}_{len(handled) + 1}",
            email_id=email.id,
            customer_id=email.customer_id,
            specialist=route(intent),
            intent=intent,
            language=resolve_language(email.language, email.language),
            urgency=decision.urgency,
            summary=decision.summary,
            order_id=resolve_order_id(email.order_id, None),
        )
        return Command(
            update={
                "envelope": envelope_to_payload(envelope),
                "handled": [intent.value],
                "supervisor_calls": calls,
            },
            goto=envelope.specialist.value,
        )

    async def run(self, email: SupportEmail) -> SupervisedRun:
        config: Any = {"configurable": {"thread_id": case_id_for(email)}}
        # Cada paso son dos super-steps (supervisor + especialista): límite con margen.
        config["recursion_limit"] = 2 * self._max_steps + 4
        snapshot = await self.graph.aget_state(config)
        # Idempotente por email, como el router: un duplicado no vuelve a ejecutar pasos.
        result = snapshot.values or await self.graph.ainvoke(
            {"email": email_to_payload(email)}, config
        )
        return {
            "outcomes": [outcome_from_payload(o) for o in result.get("outcomes", [])],
            "supervisor_calls": int(result.get("supervisor_calls", 0)),
            "stop": str(result.get("stop", "")),
        }


class SupervisedRun(TypedDict):
    outcomes: list[CaseOutcome]
    supervisor_calls: int
    stop: str


def _specialist_node(agent: SpecialistAgent) -> Any:
    async def handle(state: SupervisorState) -> SupervisorState:
        outcome = await agent.handle(envelope_from_payload(state["envelope"]))
        return {"outcomes": [outcome_to_payload(outcome)]}

    return handle


def _supervisor_input(email: SupportEmail, handled: list[str], outcomes: list[Payload]) -> str:
    # El supervisor ve el estado de cada paso, no las respuestas: menos tokens por vuelta.
    steps = zip(handled, outcomes, strict=False)
    done = "\n".join(f"- {intent}: {o['status']}" for intent, o in steps) or "- ninguno"
    return (
        f"<email>\nAsunto: {email.subject}\n\n{email.body}\n</email>\n\nPasos ya atendidos:\n{done}"
    )

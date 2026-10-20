"""Composición de los tres especialistas con los guardrails de la fase 8."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from booking_agent.flavors import create_agent
from booking_agent.guardrails import guarded_agent, guarded_executor
from booking_agent.runtime import AgentDeps
from booking_agent.spec import AgentSpec
from harness.approvals import ApprovalService
from harness.pii import PiiDetector
from harness.rules.case_reply import ReplySender, build_case_reply_tools
from harness.security import HeuristicShield
from headache_core.application.agents import SkillCatalog
from headache_core.application.agents.runtime import AgentRuntime
from headache_core.application.orchestration import SpecialistAgent
from headache_core.application.retrieval import RetrievalService
from headache_core.application.tools import ToolRegistry, build_ticketing_tools
from headache_core.domain import Specialist
from headache_core.ports import LLMPort, TicketingPort
from headache_core.ports.approvals import ApprovalStore
from headache_core.ports.guardrails import ContentShieldPort
from headache_core.ports.memory import CheckpointStore, PreferenceStore
from orchestrator.specialists.agent import AgentSpecialist
from orchestrator.specialists.creative import CreativeSpecialist
from orchestrator.specialists.specs import build_knowledge_spec, build_refunds_spec


@dataclass(frozen=True, slots=True)
class SpecialistDeps:
    llm: LLMPort
    ticketing: TicketingPort
    # None solo donde no hace falta (el contenedor del Creativo no carga el conocimiento).
    retrieval: RetrievalService | None
    # Memoria compartida: los tres leen las preferencias del cliente; solo Conocimiento escribe.
    preferences: PreferenceStore
    checkpoints: CheckpointStore
    approvals: ApprovalStore
    send_reply: ReplySender
    shield: ContentShieldPort | None = None
    pii: PiiDetector | None = None
    flavor: str = "native"
    skills: SkillCatalog | None = None
    fail_open: bool = False


def approval_registry(deps: SpecialistDeps) -> ToolRegistry:
    """Tools que solo ejecuta `ApprovalService` tras la aprobación de una persona."""
    return ToolRegistry(
        [*build_ticketing_tools(deps.ticketing), *build_case_reply_tools(deps.send_reply)]
    )


def build_approval_service(deps: SpecialistDeps) -> ApprovalService:
    return ApprovalService(deps.approvals, approval_registry(deps))


def _agent_factory(spec: AgentSpec, deps: SpecialistDeps) -> Callable[[], AgentRuntime]:
    shield = deps.shield or HeuristicShield()

    def create() -> AgentRuntime:
        executor = guarded_executor(spec, deps.approvals, shield, fail_open=deps.fail_open)
        agent_deps = AgentDeps(spec, deps.llm, executor, deps.checkpoints, deps.preferences)
        agent = create_agent(deps.flavor, agent_deps)
        return guarded_agent(agent, shield=shield, pii=deps.pii, fail_open=deps.fail_open)

    return create


def build_specialist(specialist: Specialist, deps: SpecialistDeps) -> SpecialistAgent:
    """Un solo especialista: cada contenedor A2A construye (y carga) solo lo que necesita."""
    if specialist is Specialist.CREATIVE:
        return CreativeSpecialist(
            deps.llm,
            build_approval_service(deps),
            deps.shield or HeuristicShield(),
            ticketing=deps.ticketing,
            fail_open=deps.fail_open,
        )
    if deps.retrieval is None:
        raise ValueError(f"El especialista {specialist} necesita la base de conocimiento.")
    ticketing_tools = build_ticketing_tools(deps.ticketing)
    if specialist is Specialist.REFUNDS:
        spec = build_refunds_spec(
            ticketing_tools=ticketing_tools, retrieval=deps.retrieval, skills=deps.skills
        )
    else:
        spec = build_knowledge_spec(
            ticketing_tools=ticketing_tools,
            retrieval=deps.retrieval,
            preferences=deps.preferences,
            skills=deps.skills,
        )
    return AgentSpecialist(specialist, _agent_factory(spec, deps))


def build_specialists(deps: SpecialistDeps) -> Mapping[Specialist, SpecialistAgent]:
    return {specialist: build_specialist(specialist, deps) for specialist in Specialist}

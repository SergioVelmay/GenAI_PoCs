"""Especificación única del Knowledge & Booking Agent: la comparten los cinco sabores.

Cada framework recibe exactamente las mismas instrucciones, tools, skills y política de contexto:
lo único que cambia entre sabores es quién orquesta el bucle y gestiona el estado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from booking_agent.prompts import PROMPTS
from headache_core.application.agents import (
    ContextPolicy,
    SkillCatalog,
    build_memory_tools,
    build_skill_tools,
)
from headache_core.application.retrieval import RetrievalService
from headache_core.application.tools import FALLBACK_ANSWER, ToolRegistry
from headache_core.application.tools.knowledge import build_knowledge_tools
from headache_core.ports.memory import PreferenceStore

SKILLS_DIR = Path(__file__).parent / "skills"

BOOKING_AGENT_PROMPT = PROMPTS.get("booking-agent").text


@dataclass(frozen=True, slots=True)
class AgentSpec:
    name: str
    instructions: str
    tools: ToolRegistry
    skills: SkillCatalog
    context: ContextPolicy = field(default_factory=ContextPolicy)
    max_iterations: int = 6
    # Respuesta al agotar max_iterations: la misma en todos los sabores.
    fallback_answer: str = FALLBACK_ANSWER

    @property
    def system_prompt(self) -> str:
        """Instrucciones + índice de skills: estable para todos los clientes (prefijo cacheable)."""
        index = self.skills.index()
        return f"{self.instructions}\n{index}\n" if index else self.instructions


def build_booking_spec(
    *,
    ticketing_tools: ToolRegistry,
    retrieval: RetrievalService,
    preferences: PreferenceStore,
    skills: SkillCatalog | None = None,
    context: ContextPolicy | None = None,
    max_iterations: int = 6,
    instructions: str = BOOKING_AGENT_PROMPT,
) -> AgentSpec:
    """`instructions`: solo para experimentos A/B (fase 12.7) con un prompt candidato."""
    catalog = skills or SkillCatalog.from_directory(SKILLS_DIR)
    tools = ToolRegistry(
        [
            *ticketing_tools,
            *build_knowledge_tools(retrieval),
            *build_skill_tools(catalog),
            *build_memory_tools(preferences),
        ]
    )
    return AgentSpec(
        name="headache-booking-agent",
        instructions=instructions,
        tools=tools,
        skills=catalog,
        context=context or ContextPolicy(),
        max_iterations=max_iterations,
    )

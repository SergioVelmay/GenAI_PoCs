"""Especificaciones de los especialistas con tools: mínimo privilegio por construcción.

Cada especialista recibe SOLO las tools y skills de su trabajo. Aunque un prompt injection
convenza al de Conocimiento de reembolsar, no tiene `request_refund`; y solo él puede escribir
preferencias en la memoria a largo plazo (los demás la leen).
"""

from __future__ import annotations

from collections.abc import Iterable

from booking_agent.spec import SKILLS_DIR, AgentSpec
from headache_core.application.agents import (
    ContextPolicy,
    SkillCatalog,
    build_memory_tools,
    build_skill_tools,
)
from headache_core.application.retrieval import RetrievalService
from headache_core.application.tools import (
    CATALOG_TOOLS,
    ORDER_TOOLS,
    REFUND_TOOLS,
    ToolRegistry,
)
from headache_core.application.tools.knowledge import build_knowledge_tools
from headache_core.ports.memory import PreferenceStore
from orchestrator.prompts import PROMPTS

REFUNDS_PROMPT = PROMPTS.get("refunds-specialist").text

KNOWLEDGE_PROMPT = PROMPTS.get("knowledge-specialist").text

REFUNDS_SKILLS = ("refund-handling",)
KNOWLEDGE_SKILLS = ("knowledge-answers", "booking-assistance")


def skill_subset(catalog: SkillCatalog, names: Iterable[str]) -> SkillCatalog:
    skills = []
    for name in names:
        skill = catalog.get(name)
        if skill is None:
            raise ValueError(f"Skill desconocida: {name}.")
        skills.append(skill)
    return SkillCatalog(skills)


def build_refunds_spec(
    *,
    ticketing_tools: ToolRegistry,
    retrieval: RetrievalService,
    skills: SkillCatalog | None = None,
    context: ContextPolicy | None = None,
) -> AgentSpec:
    catalog = skill_subset(skills or SkillCatalog.from_directory(SKILLS_DIR), REFUNDS_SKILLS)
    tools = ToolRegistry(
        [
            *ticketing_tools.subset((*ORDER_TOOLS, *REFUND_TOOLS)),
            *build_knowledge_tools(retrieval),
            *build_skill_tools(catalog),
        ]
    )
    return AgentSpec(
        name="headache-refunds-specialist",
        instructions=REFUNDS_PROMPT,
        tools=tools,
        skills=catalog,
        context=context or ContextPolicy(),
    )


def build_knowledge_spec(
    *,
    ticketing_tools: ToolRegistry,
    retrieval: RetrievalService,
    preferences: PreferenceStore,
    skills: SkillCatalog | None = None,
    context: ContextPolicy | None = None,
) -> AgentSpec:
    catalog = skill_subset(skills or SkillCatalog.from_directory(SKILLS_DIR), KNOWLEDGE_SKILLS)
    tools = ToolRegistry(
        [
            *ticketing_tools.subset((*CATALOG_TOOLS, *ORDER_TOOLS)),
            *build_knowledge_tools(retrieval),
            *build_skill_tools(catalog),
            *build_memory_tools(preferences),
        ]
    )
    return AgentSpec(
        name="headache-knowledge-specialist",
        instructions=KNOWLEDGE_PROMPT,
        tools=tools,
        skills=catalog,
        context=context or ContextPolicy(),
    )

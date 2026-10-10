"""Sabores del Knowledge & Booking Agent. Todos implementan `AgentRuntime` sobre `AgentDeps`.

Los de framework se importan de forma perezosa: cada uno arrastra dependencias pesadas.
"""

from __future__ import annotations

from importlib import import_module

from booking_agent.runtime import AgentDeps, AgentRuntime

FLAVORS: dict[str, str] = {
    "native": "booking_agent.flavors.native",
    "langgraph": "booking_agent.flavors.langgraph",
    "llamaindex": "booking_agent.flavors.llamaindex",
    "maf": "booking_agent.flavors.maf",
    "strands": "booking_agent.flavors.strands",
    # Carril A: sin nube, Foundry usa un emulador y AgentCore su contrato HTTP en proceso.
    "foundry": "booking_agent.flavors.foundry",
    "agentcore": "booking_agent.flavors.agentcore",
}
MANAGED_FLAVORS = frozenset({"foundry", "agentcore"})


def create_agent(flavor: str, deps: AgentDeps) -> AgentRuntime:
    if flavor not in FLAVORS:
        raise ValueError(f"Sabor desconocido: {flavor}. Disponibles: {', '.join(FLAVORS)}")
    module = import_module(FLAVORS[flavor])
    agent: AgentRuntime = module.create(deps)
    return agent

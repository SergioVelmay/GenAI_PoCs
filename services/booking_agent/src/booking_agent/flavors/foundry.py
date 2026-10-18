"""Sabor carril A (Azure): Foundry Agent Service con *function tools* ejecutadas en el cliente.

El bucle es del servicio (pide tools, recibe resultados y sigue la conversación), pero cada
ronda vuelve al cliente: aquí se cuentan las iteraciones y se ejecutan las tools con el
`JournaledExecutor`. Sin nube, `create` usa `LocalAgentService` con la misma definición.
"""

from __future__ import annotations

import asyncio

from booking_agent.managed.foundry import (
    AgentServiceClient,
    LocalAgentService,
    agent_definition,
    function_output,
    message,
)
from booking_agent.runtime import AgentDeps, AgentTurn
from headache_core.application.agents import render_session_context
from headache_core.application.tools import ToolContext
from headache_core.ports.memory import ThreadAccessError

LOCAL_MODEL = "local-emulator"


class FoundryAgent:
    flavor = "foundry"

    def __init__(self, deps: AgentDeps, service: AgentServiceClient) -> None:
        self._deps = deps
        self._service = service
        # hilo -> (dueño, conversación). En producción, tabla propia: el servicio no sabe de dueños.
        self._threads: dict[str, tuple[str, str]] = {}

    async def _conversation(self, thread_id: str, customer_id: str) -> str:
        if (known := self._threads.get(thread_id)) is not None:
            if known[0] != customer_id:
                raise ThreadAccessError(f"Hilo {thread_id} no encontrado.")
            return known[1]
        conversation_id = await self._service.create_conversation()
        self._threads[thread_id] = (customer_id, conversation_id)
        return conversation_id

    async def respond(self, thread_id: str, message_text: str, context: ToolContext) -> AgentTurn:
        deps = self._deps
        conversation = await self._conversation(thread_id, context.customer_id)
        session = render_session_context(await deps.preferences.get(context.customer_id), "")
        items = [message("developer", session)] if session else []
        items.append(message("user", message_text))
        for _ in range(deps.spec.max_iterations):
            reply = await self._service.respond(conversation, items)
            if not reply.calls:
                return AgentTurn(reply.text, "answer")
            executions = await asyncio.gather(
                *(deps.executor.execute(call, context) for call in reply.calls)
            )
            items = [function_output(e.call.id, e.output) for e in executions]
        # Los resultados de la última ronda y el fallback se añaden sin pedir otra respuesta.
        await self._service.append(
            conversation, [*items, message("assistant", deps.spec.fallback_answer)]
        )
        return AgentTurn(deps.spec.fallback_answer, "max_iterations")


def create(deps: AgentDeps) -> FoundryAgent:
    """Por defecto, el emulador local (sin nube): mismo bucle y misma definición del agente."""
    definition = agent_definition(deps.spec.system_prompt, deps.spec.tools, model=LOCAL_MODEL)
    return FoundryAgent(deps, LocalAgentService(deps.llm, definition))


def create_cloud(deps: AgentDeps, service: AgentServiceClient) -> FoundryAgent:
    """Contra Foundry Agent Service real (`FoundryAgentService`): el agente ya debe estar
    publicado con `scripts/provision_foundry_agent.py --apply` desde el mismo `AgentSpec`."""
    return FoundryAgent(deps, service)

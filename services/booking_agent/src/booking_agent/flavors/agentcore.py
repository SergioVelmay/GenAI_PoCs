"""Sabor carril A (AWS): el agente detrás del contrato de Bedrock AgentCore Runtime.

El cliente solo conoce `InvokeAgentRuntime(sesión, usuario, prompt)`; dentro del contenedor corre
un sabor de framework (Strands, el nativo de AgentCore). Sin nube, `create` monta la app del
contenedor en proceso (ASGI) y la invoca por HTTP: se prueba el mismo contrato que en la nube.
"""

from __future__ import annotations

import httpx

from booking_agent.managed.agentcore import (
    HttpRuntimeInvoker,
    RuntimeInvoker,
    create_runtime_app,
    runtime_session_id,
)
from booking_agent.runtime import AgentDeps, AgentTurn
from headache_core.application.tools import ToolContext

HOSTED_FLAVOR = "strands"


class AgentCoreAgent:
    flavor = "agentcore"

    def __init__(self, invoker: RuntimeInvoker) -> None:
        self._invoker = invoker

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        result = await self._invoker.invoke(
            session_id=runtime_session_id(context.customer_id, thread_id),
            user_id=context.customer_id,
            prompt=message,
        )
        return AgentTurn(result.response, result.stop_reason, result.compacted)


def create(deps: AgentDeps) -> AgentCoreAgent:
    from booking_agent.flavors import create_agent

    app = create_runtime_app(create_agent(HOSTED_FLAVOR, deps))
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://agentcore.local"
    )
    return AgentCoreAgent(HttpRuntimeInvoker(client))

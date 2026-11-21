"""Sabor carril A (GCP): el agente detrás del contrato de Agent Runtime (7.G3).

El cliente solo conoce `query(usuario, sesión, mensaje)`; dentro del contenedor corre `adk`, el
SDK nativo de GCP (como AgentCore aloja `strands`). Sin nube, `create` monta la app del
contenedor en proceso (ASGI) y la invoca por HTTP: se prueba el mismo contrato que en la nube.

`create` **avisa** (WARNING) de que usa el emulador: en la fase 7 el primer recorrido de
extremo a extremo de Foundry usó el emulador local sin que nadie lo notara. `create_cloud`
habla con Agent Runtime real (`AgentRuntimeClient`).
"""

from __future__ import annotations

import logging

import httpx

from booking_agent.managed.agent_runtime import (
    HttpRuntimeClient,
    RuntimeClient,
    create_runtime_app,
    runtime_session_id,
)
from booking_agent.runtime import AgentDeps, AgentTurn
from genaipocs_core.application.tools import ToolContext

HOSTED_FLAVOR = "adk"
logger = logging.getLogger(__name__)
_warned = False


class AgentPlatformAgent:
    flavor = "agentplatform"

    def __init__(self, client: RuntimeClient) -> None:
        self._client = client

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        output = await self._client.query(
            user_id=context.customer_id,
            session_id=runtime_session_id(context.customer_id, thread_id),
            message=message,
        )
        return AgentTurn(output.response, output.stop_reason or "answer", output.compacted)


def create(deps: AgentDeps) -> AgentPlatformAgent:
    from booking_agent.flavors import create_agent

    global _warned
    if not _warned:  # Una vez por proceso: la suite crea un agente por escenario.
        logger.warning(
            "agentplatform: emulador local en proceso (contrato de Agent Runtime con %s), "
            "no toca la nube; para Agent Runtime real, create_cloud",
            HOSTED_FLAVOR,
        )
        _warned = True
    app = create_runtime_app(create_agent(HOSTED_FLAVOR, deps))
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://agent-runtime.local"
    )
    return AgentPlatformAgent(HttpRuntimeClient(client))


def create_cloud(client: RuntimeClient) -> AgentPlatformAgent:
    """Contra Agent Runtime real (`AgentRuntimeClient`): el contenedor ya está desplegado
    (`agent_platform.tf`, 7.G4) con su memoria en Sessions y Memory Bank."""
    return AgentPlatformAgent(client)

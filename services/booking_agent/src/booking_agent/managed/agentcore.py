"""Carril A (AWS): el agente desplegado en Bedrock AgentCore Runtime.

AgentCore Runtime no impone framework: ejecuta NUESTRO contenedor (aquí, cualquier sabor) en una
microVM por sesión, siempre que cumpla el contrato HTTP: `POST /invocations` y `GET /ping` en el
puerto 8080. Este módulo tiene las dos mitades:
- `create_runtime_app`: el servidor que va dentro del contenedor.
- `RuntimeInvoker`: el cliente. `BotoRuntimeInvoker` llama a `InvokeAgentRuntime` (nube) y
  `HttpRuntimeInvoker` habla el mismo contrato con el contenedor local o en proceso (ASGI).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from booking_agent.runtime import AgentRuntime
from headache_core.application.tools import ToolContext
from headache_core.ports.memory import ThreadAccessError

if TYPE_CHECKING:
    from mypy_boto3_bedrock_agentcore import BedrockAgentCoreClient

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
# Lo rellena AgentCore con el `runtimeUserId` de la llamada, que solo puede fijar un llamante
# con permiso `InvokeAgentRuntimeForUser` (nuestro backend autenticado), nunca el cliente final.
USER_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-User-Id"


def runtime_session_id(customer_id: str, thread_id: str) -> str:
    """AgentCore exige >= 33 caracteres; derivarlo del cliente aísla las microVM por cliente."""
    digest = hashlib.sha256(f"{customer_id}\n{thread_id}".encode()).hexdigest()
    return f"headache-{digest}"


class InvocationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class InvocationResponse(BaseModel):
    response: str
    status: Literal["success"] = "success"
    stop_reason: Literal["answer", "max_iterations"]
    compacted: bool


def create_runtime_app(agent: AgentRuntime) -> FastAPI:
    app = FastAPI(title=f"Headache booking agent ({agent.flavor}) - AgentCore Runtime")

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "Healthy"}

    @app.post("/invocations")
    async def invoke(
        body: InvocationRequest,
        session_id: Annotated[str, Header(alias=SESSION_HEADER, min_length=33)],
        user_id: Annotated[str, Header(alias=USER_HEADER, min_length=1)],
    ) -> InvocationResponse:
        context = ToolContext(customer_id=user_id, request_id=session_id)
        try:
            turn = await agent.respond(session_id, body.prompt, context)
        except ThreadAccessError as exc:
            raise HTTPException(status_code=404, detail="Sesión no encontrada.") from exc
        return InvocationResponse(
            response=turn.answer, stop_reason=turn.stop_reason, compacted=turn.compacted
        )

    return app


class RuntimeInvoker(Protocol):
    async def invoke(self, *, session_id: str, user_id: str, prompt: str) -> InvocationResponse: ...


class HttpRuntimeInvoker:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def invoke(self, *, session_id: str, user_id: str, prompt: str) -> InvocationResponse:
        response = await self._client.post(
            "/invocations",
            json={"prompt": prompt},
            headers={SESSION_HEADER: session_id, USER_HEADER: user_id},
        )
        if response.status_code == 404:
            raise ThreadAccessError("Sesión no encontrada.")
        response.raise_for_status()
        return InvocationResponse.model_validate(response.json())


class BotoRuntimeInvoker:
    def __init__(self, client: BedrockAgentCoreClient, agent_runtime_arn: str) -> None:
        self._client = client
        self._arn = agent_runtime_arn

    async def invoke(self, *, session_id: str, user_id: str, prompt: str) -> InvocationResponse:
        response = await asyncio.to_thread(
            self._client.invoke_agent_runtime,
            agentRuntimeArn=self._arn,
            runtimeSessionId=session_id,
            runtimeUserId=user_id,
            contentType="application/json",
            accept="application/json",
            payload=json.dumps({"prompt": prompt}).encode(),
        )
        body: Any = json.loads(response["response"].read())
        return InvocationResponse.model_validate(body)

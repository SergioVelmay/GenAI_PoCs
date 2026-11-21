"""Carril A (GCP): el agente desplegado en Agent Runtime (antes Agent Engine) con su contenedor.

Como AgentCore Runtime, Agent Runtime no impone framework: ejecuta NUESTRO contenedor (aquí,
el sabor `adk`) si cumple el contrato HTTP en el puerto 8080: `POST /api/reasoning_engine` con
`{"class_method", "input"}` y respuesta `{"output": ...}` (y `GET /` como comprobación de
salud). El cliente lo invoca con `reasoningEngines.query`. Este módulo tiene las dos mitades:
- `create_runtime_app`: el servidor que va dentro del contenedor.
- `RuntimeClient`: `AgentRuntimeClient` llama a `:query` en la nube y `HttpRuntimeClient` habla
  el mismo contrato con el contenedor local o en proceso (ASGI).

Identidad: el contrato no lleva cabeceras de usuario (AgentCore sí: `runtimeUserId`, que solo
fija quien tiene `InvokeAgentRuntimeForUser`). Aquí el cliente viaja en `input.user_id` y lo
puede poner cualquiera con permiso `aiplatform.reasoningEngines.query`: ese permiso solo lo
tiene la cuenta de servicio del backend autenticado, nunca un cliente final.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from booking_agent.runtime import AgentRuntime, StopReason
from genaipocs_core.application.tools import ToolContext
from genaipocs_core.config import GcpSettings, Settings
from genaipocs_core.ports.memory import ThreadAccessError

CLASS_METHOD = "query"
NOT_FOUND = "thread_not_found"


def runtime_session_id(customer_id: str, thread_id: str) -> str:
    """Derivarlo del cliente aísla los hilos por cliente aunque elija el mismo identificador."""
    digest = hashlib.sha256(f"{customer_id}\n{thread_id}".encode()).hexdigest()
    return f"genaipocs-{digest}"


class QueryInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)


class QueryRequest(BaseModel):
    class_method: str
    input: QueryInput


class QueryOutput(BaseModel):
    response: str = ""
    stop_reason: StopReason | None = None
    compacted: bool = False
    # El error de negocio va dentro de `output`: qué hace el plano de control de Agent Runtime
    # con un 4xx del contenedor no está documentado (se comprueba en la nube).
    error: Literal["thread_not_found"] | None = None


def create_runtime_app(agent: AgentRuntime) -> FastAPI:
    app = FastAPI(title=f"Genaipocs booking agent ({agent.flavor}) - Agent Runtime")

    @app.get("/")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/reasoning_engine")
    async def query(body: QueryRequest) -> dict[str, QueryOutput]:
        if body.class_method != CLASS_METHOD:
            raise HTTPException(status_code=400, detail=f"Método desconocido: {body.class_method}")
        request = body.input
        context = ToolContext(customer_id=request.user_id, request_id=request.session_id)
        try:
            turn = await agent.respond(request.session_id, request.message, context)
        except ThreadAccessError:
            return {"output": QueryOutput(error=NOT_FOUND)}
        output = QueryOutput(
            response=turn.answer, stop_reason=turn.stop_reason, compacted=turn.compacted
        )
        return {"output": output}

    return app


class RuntimeClient(Protocol):
    async def query(self, *, user_id: str, session_id: str, message: str) -> QueryOutput: ...


def _output(payload: Any) -> QueryOutput:
    output = QueryOutput.model_validate(payload["output"])
    if output.error == NOT_FOUND:
        raise ThreadAccessError("Sesión no encontrada.")
    return output


def _input(user_id: str, session_id: str, message: str) -> dict[str, Any]:
    return QueryInput(user_id=user_id, session_id=session_id, message=message).model_dump()


class HttpRuntimeClient:
    """El contrato del contenedor tal cual (local o en proceso)."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def query(self, *, user_id: str, session_id: str, message: str) -> QueryOutput:
        response = await self._client.post(
            "/api/reasoning_engine",
            json={"class_method": CLASS_METHOD, "input": _input(user_id, session_id, message)},
        )
        response.raise_for_status()
        return _output(response.json())


def adc_token(settings: GcpSettings) -> Callable[[], Awaitable[str]]:
    """Token de acceso de ADC (o de la cuenta suplantada), renovado cuando caduca."""
    import google.auth
    from google.auth.transport.requests import Request

    from genaipocs_core.adapters.gcp_auth import CLOUD_PLATFORM_SCOPE, gcp_credentials

    credentials = gcp_credentials(settings)
    if credentials is None:
        credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])

    async def token() -> str:
        if not credentials.valid:
            # `refresh` es síncrono (requests): fuera del bucle de eventos.
            await asyncio.to_thread(credentials.refresh, Request())
        return str(credentials.token)

    return token


class AgentRuntimeClient:
    """`reasoningEngines.query` (REST v1) con un token de ADC; sin SDK de por medio."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        engine: str,
        token: Callable[[], Awaitable[str]],
    ) -> None:
        # `projects/<p>/locations/<región>/reasoningEngines/<id>`: el endpoint es regional.
        self._region = engine.split("/")[3]
        self._engine = engine
        self._client = client
        self._token = token

    async def query(self, *, user_id: str, session_id: str, message: str) -> QueryOutput:
        response = await self._client.post(
            f"https://{self._region}-aiplatform.googleapis.com/v1/{self._engine}:query",
            json={"classMethod": CLASS_METHOD, "input": _input(user_id, session_id, message)},
            headers={"Authorization": f"Bearer {await self._token()}"},
        )
        response.raise_for_status()
        return _output(response.json())


def build_agent_runtime_client(settings: Settings) -> AgentRuntimeClient:
    """Composición con la nube: el agente desplegado en `GCP_AGENT_PLATFORM__RUNTIME_ENGINE`."""
    engine = settings.gcp_agent_platform.runtime_engine
    if not engine:
        raise ValueError("Falta GCP_AGENT_PLATFORM__RUNTIME_ENGINE (agente desplegado, fase 15).")
    return AgentRuntimeClient(httpx.AsyncClient(timeout=60), engine, adc_token(settings.gcp))

"""Cliente A2A que cumple `SpecialistAgent`: LangGraph y MAF lo usan sin saber que es remoto.

No se fía del servidor: comprueba la Agent Card, que la tarea terminó COMPLETED con un único
artefacto `CaseOutcome` y que la respuesta es del mismo caso y del mismo especialista.
"""

from __future__ import annotations

import uuid

import httpx
from a2a import helpers
from a2a.client import A2ACardResolver, Client, ClientConfig, create_client
from a2a.types import a2a_pb2 as pb

from headache_core.application.orchestration import (
    InvalidHandoffError,
    envelope_to_payload,
    outcome_from_payload,
)
from headache_core.application.telemetry import genai, get_telemetry
from headache_core.domain import CaseEnvelope, CaseOutcome, Specialist
from headache_core.ports.telemetry import SpanKind
from orchestrator.remote.server import OUTCOME_ARTIFACT, card_name
from orchestrator.remote.tracing import propagate_trace


class RemoteSpecialistError(RuntimeError):
    """El especialista remoto falló o devolvió algo que no cumple el contrato."""


class RemoteSpecialist:
    def __init__(self, specialist: Specialist, client: Client) -> None:
        self._specialist = specialist
        self._client = client

    @classmethod
    async def connect(
        cls, specialist: Specialist, base_url: str, http: httpx.AsyncClient
    ) -> RemoteSpecialist:
        """Descubre el agente por su Agent Card y comprueba que es quien dice ser."""
        card = await A2ACardResolver(http, base_url).get_agent_card()
        if card.name != card_name(specialist):
            raise RemoteSpecialistError(f"{base_url} no es el especialista {specialist}.")
        propagate_trace(http)
        client = await create_client(card, ClientConfig(httpx_client=http, streaming=False))
        return cls(specialist, client)

    @property
    def specialist(self) -> Specialist:
        return self._specialist

    async def handle(self, envelope: CaseEnvelope) -> CaseOutcome:
        if envelope.specialist is not self._specialist:
            raise InvalidHandoffError(
                f"Caso para {envelope.specialist}, no para {self._specialist}."
            )
        message = helpers.new_data_message(envelope_to_payload(envelope), role=pb.Role.ROLE_USER)
        message.message_id = str(uuid.uuid4())
        task: pb.Task | None = None
        # Agente remoto: span CLIENT; el SERVER del especialista y su `invoke_agent` cuelgan de él.
        with get_telemetry().span(
            genai.span_name(genai.GenAIOperation.INVOKE_AGENT, self._specialist.value),
            kind=SpanKind.CLIENT,
            attributes={
                genai.OPERATION_NAME: genai.GenAIOperation.INVOKE_AGENT.value,
                genai.AGENT_NAME: self._specialist.value,
                genai.PROVIDER_NAME: "a2a",
                "headache.correlation_id": envelope.case_id,
            },
        ):
            request = pb.SendMessageRequest(message=message)
            async for response in self._client.send_message(request):
                if response.HasField("task"):
                    task = response.task
        if task is None:
            raise RemoteSpecialistError("El especialista no devolvió una tarea.")
        state = task.status.state
        if state == pb.TaskState.TASK_STATE_REJECTED:
            raise InvalidHandoffError(_status_text(task) or "Traspaso rechazado.")
        if state != pb.TaskState.TASK_STATE_COMPLETED:
            raise RemoteSpecialistError(f"Tarea en estado {pb.TaskState.Name(state)}.")
        artifacts = [a for a in task.artifacts if a.name == OUTCOME_ARTIFACT]
        parts = helpers.get_data_parts(list(artifacts[0].parts)) if len(artifacts) == 1 else []
        if len(parts) != 1 or not isinstance(parts[0], dict):
            raise RemoteSpecialistError("Respuesta sin un único CaseOutcome.")
        outcome = outcome_from_payload(parts[0])
        if outcome.case_id != envelope.case_id or outcome.specialist is not self._specialist:
            raise RemoteSpecialistError("La respuesta no corresponde a este caso.")
        return outcome

    async def close(self) -> None:
        await self._client.close()


def _status_text(task: pb.Task) -> str:
    if not task.status.HasField("message"):
        return ""
    return " ".join(helpers.get_text_parts(list(task.status.message.parts)))

"""Un especialista como microservicio A2A (`a2a-sdk` 1.x, protocolo A2A 1.0, JSON-RPC).

    POST /a2a  (SendMessage)  mensaje con un DataPart = CaseEnvelope
                              → tarea COMPLETED con un artefacto DataPart = CaseOutcome
                              → tarea REJECTED si el traspaso es inválido
    GET  /.well-known/agent-card.json   descubrimiento (pública)

A2A es a los agentes lo que MCP es a las tools: MCP expone capacidades a UN agente; A2A
deja que agentes independientes (otro equipo, otro framework, otra nube) se deleguen tareas
opacas. El especialista no expone sus tools, su prompt ni su memoria: solo su Agent Card.
"""

from __future__ import annotations

import logging
from typing import Any

from a2a import helpers
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskStore, TaskUpdater
from a2a.types import a2a_pb2 as pb
from a2a.utils.constants import PROTOCOL_VERSION_1_0
from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from headache_core.application.orchestration import (
    InvalidHandoffError,
    SpecialistAgent,
    envelope_from_payload,
    outcome_to_payload,
)
from headache_core.domain import ROUTES, Specialist
from orchestrator.remote.auth import BearerAuthMiddleware
from orchestrator.remote.tracing import TraceContextMiddleware

logger = logging.getLogger(__name__)

RPC_PATH = "/a2a"
JSON = "application/json"
OUTCOME_ARTIFACT = "case_outcome"
BEARER = "bearer"

DESCRIPTIONS: dict[Specialist, str] = {
    Specialist.REFUNDS: "Resuelve solicitudes de reembolso de pedidos de Headache aplicando la "
    "política; cada reembolso queda pendiente de aprobación humana.",
    Specialist.CREATIVE: "Redacta respuestas a quejas de clientes y a propuestas de partners con "
    "plantilla y tono controlados; todo envío queda pendiente de aprobación humana.",
    Specialist.KNOWLEDGE: "Responde dudas sobre eventos, entradas y cambios de sesión con la base "
    "de conocimiento y el catálogo de Headache.",
}


def card_name(specialist: Specialist) -> str:
    return f"headache-{specialist.value}-specialist"


def build_agent_card(specialist: Specialist, public_url: str) -> pb.AgentCard:
    intents = sorted(i.value for i, s in ROUTES.items() if s is specialist)
    return pb.AgentCard(
        name=card_name(specialist),
        description=DESCRIPTIONS[specialist],
        version="1.0.0",
        provider=pb.AgentProvider(organization="Headache", url="https://headache.example"),
        supported_interfaces=[
            pb.AgentInterface(
                url=f"{public_url.rstrip('/')}{RPC_PATH}",
                protocol_binding="JSONRPC",
                protocol_version=PROTOCOL_VERSION_1_0,
            )
        ],
        # Una llamada = un caso: sin streaming ni push (el asíncrono va por la cola, fase 9.6).
        capabilities=pb.AgentCapabilities(streaming=False, push_notifications=False),
        security_schemes={
            BEARER: pb.SecurityScheme(
                http_auth_security_scheme=pb.HTTPAuthSecurityScheme(
                    scheme="Bearer",
                    bearer_format="JWT",
                    description="Token de servicio con el scope cases:handle.",
                )
            )
        },
        security_requirements=[pb.SecurityRequirement(schemes={BEARER: pb.StringList()})],
        default_input_modes=[JSON],
        default_output_modes=[JSON],
        skills=[
            pb.AgentSkill(
                id=f"handle-{specialist.value}-case",
                name=f"Caso de {specialist.value}",
                description=DESCRIPTIONS[specialist],
                tags=intents,
                input_modes=[JSON],
                output_modes=[JSON],
            )
        ],
    )


class SpecialistExecutor(AgentExecutor):
    """Adapta `SpecialistAgent.handle` al ciclo de vida de una tarea A2A."""

    def __init__(self, agent: SpecialistAgent) -> None:
        self._agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        assert message is not None
        task = context.current_task or helpers.new_task_from_user_message(message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        try:
            envelope = envelope_from_payload(_single_data_part(message))
            outcome = await self._agent.handle(envelope)
        except InvalidHandoffError as exc:
            # Traspaso inválido: rechazo explícito, el llamante no debe reintentar.
            logger.warning("Traspaso rechazado: %s", exc)
            await updater.reject(updater.new_agent_message([helpers.new_text_part(str(exc))]))
            return
        await updater.add_artifact(
            [helpers.new_data_part(outcome_to_payload(outcome))], name=OUTCOME_ARTIFACT
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Un caso no se cancela a medias: los efectos ya están protegidos por aprobaciones.
        raise NotImplementedError("Los casos no admiten cancelación.")


def _single_data_part(message: pb.Message) -> dict[str, Any]:
    parts = helpers.get_data_parts(list(message.parts))
    if len(parts) != 1 or not isinstance(parts[0], dict):
        raise InvalidHandoffError("El mensaje debe traer un único DataPart con el traspaso.")
    return parts[0]


def create_specialist_app(
    agent: SpecialistAgent,
    *,
    public_url: str,
    verifier: JWTVerifier | None,
    scope: str = "cases:handle",
    allowed_callers: tuple[str, ...] = ("svc-orchestrator",),
    task_store: TaskStore | None = None,
) -> Starlette:
    """`verifier=None` solo para tests en proceso: en un contenedor siempre hay token."""
    card = build_agent_card(agent.specialist, public_url)
    handler = DefaultRequestHandler(
        SpecialistExecutor(agent), task_store or InMemoryTaskStore(), card
    )

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "specialist": agent.specialist.value})

    app = Starlette(
        routes=[
            Route("/health", health),
            *create_agent_card_routes(card),
            *create_jsonrpc_routes(handler, RPC_PATH),
        ]
    )
    if verifier is not None:
        app.add_middleware(
            BearerAuthMiddleware,
            verifier=verifier,
            scope=scope,
            allowed_callers=allowed_callers,
        )
    # El último añadido es el más externo: el span cubre también la validación del token.
    app.add_middleware(
        TraceContextMiddleware,
        specialist=agent.specialist.value,
        skip=frozenset({"/health", "/.well-known/agent-card.json"}),
    )
    return app

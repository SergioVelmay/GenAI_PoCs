"""Un especialista como servicio A2A (un contenedor por subagente). TIENE COSTE al recibir casos.

    uv run python -m orchestrator.serve refunds --port 8201
    docker compose --profile agents up -d

Dependencias reales: LLM por el gateway (fase 2), API de ticketing por HTTP, aprobaciones en
PostgreSQL (compartidas con el orquestador, que es quien revisa) y conocimiento del servicio de
retrieval (RETRIEVAL__*), solo en los especialistas que lo usan. Siempre con OAuth: sin
`A2A__PUBLIC_KEY_FILE` o `A2A__JWKS_URI` el servicio no arranca.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import uvicorn
from starlette.applications import Starlette

from booking_agent.guardrails import build_pii_detector, build_shield
from harness.rules.case_reply import SendCaseReplyArgs
from headache_core.adapters.llm_factory import build_gateway
from headache_core.adapters.memory import InMemoryCheckpointStore, InMemoryPreferenceStore
from headache_core.adapters.postgres import create_engine, create_session_factory
from headache_core.adapters.postgres.approvals import PostgresApprovalStore
from headache_core.adapters.ticketing_http import HttpTicketingClient
from headache_core.application.tools import ToolContext
from headache_core.config import Settings, get_settings
from headache_core.domain import Specialist
from orchestrator.remote import build_verifier, create_specialist_app
from orchestrator.specialists import SpecialistDeps, build_specialist
from retrieval.bootstrap import build_runtime
from retrieval.settings import RetrievalSettings

logger = logging.getLogger("orchestrator.serve")


async def log_outbox(args: SendCaseReplyArgs, context: ToolContext) -> str:
    """Envío simulado en local: el email aprobado se registra, no se manda."""
    logger.info("Email aprobado para %s: %s", context.customer_id, args.subject)
    return f"local-{args.reference}"


@asynccontextmanager
async def specialist_app(
    specialist: Specialist, settings: Settings, public_url: str
) -> AsyncIterator[Starlette]:
    verifier = build_verifier(settings.a2a, specialist.value)
    async with AsyncExitStack() as stack:
        engine = create_engine(settings.postgres, application_name=f"agent-{specialist.value}")
        stack.push_async_callback(engine.dispose)
        ticketing = await stack.enter_async_context(
            HttpTicketingClient.from_settings(settings.ticketing_api)
        )
        retrieval = None
        if specialist is not Specialist.CREATIVE:
            knowledge = await build_runtime(RetrievalSettings(), settings)
            stack.push_async_callback(knowledge.aclose)
            retrieval = knowledge.service
        shield = await stack.enter_async_context(build_shield(settings))
        deps = SpecialistDeps(
            llm=build_gateway(settings),
            ticketing=ticketing,
            retrieval=retrieval,
            # Por caso: cada caso es un hilo nuevo. Las preferencias, en memoria del proceso
            # (en la nube: AgentCore Memory / Cosmos DB, fase 13-14).
            preferences=InMemoryPreferenceStore(),
            checkpoints=InMemoryCheckpointStore(),
            approvals=PostgresApprovalStore(create_session_factory(engine)),
            send_reply=log_outbox,
            shield=shield,
            pii=build_pii_detector(settings),
            fail_open=settings.guardrails.fail_open,
        )
        yield create_specialist_app(
            build_specialist(specialist, deps),
            public_url=public_url,
            verifier=verifier,
            scope=settings.a2a.scope,
            allowed_callers=tuple(settings.a2a.allowed_callers),
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Especialista de Headache como servicio A2A")
    parser.add_argument("specialist", choices=[s.value for s in Specialist])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8200)
    parser.add_argument("--public-url", help="URL anunciada en la Agent Card")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    settings = get_settings()
    specialist = Specialist(args.specialist)
    public_url = args.public_url or f"http://127.0.0.1:{args.port}"
    async with specialist_app(specialist, settings, public_url) as app:
        server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port))
        await server.serve()


if __name__ == "__main__":
    asyncio.run(main())

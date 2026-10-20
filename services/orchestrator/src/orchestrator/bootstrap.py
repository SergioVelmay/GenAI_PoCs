"""Composición con adaptadores reales: la comparten el servidor A2A y los workers de la cola."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx

from booking_agent.guardrails import build_pii_detector, build_shield
from harness.rules.case_reply import SendCaseReplyArgs
from headache_core.adapters.llm_factory import build_gateway
from headache_core.adapters.memory import InMemoryCheckpointStore, InMemoryPreferenceStore
from headache_core.adapters.postgres import create_engine, create_session_factory
from headache_core.adapters.postgres.approvals import PostgresApprovalStore
from headache_core.adapters.postgres.cases import PostgresCaseStore
from headache_core.adapters.ticketing_http import HttpTicketingClient
from headache_core.application.orchestration import SpecialistAgent
from headache_core.application.tools import ToolContext
from headache_core.config import Settings
from headache_core.domain import Specialist
from headache_core.ports.cases import CaseStore
from headache_mcp.auth import DevTokenIssuer
from orchestrator.remote import BearerAuth, RemoteSpecialist, dev_token_source
from orchestrator.specialists import SpecialistDeps
from retrieval.bootstrap import build_runtime
from retrieval.settings import RetrievalSettings

logger = logging.getLogger(__name__)


async def log_outbox(args: SendCaseReplyArgs, context: ToolContext) -> str:
    """Envío simulado en local: el email aprobado se registra, no se manda."""
    logger.info("Email aprobado para %s: %s", context.customer_id, args.subject)
    return f"local-{args.reference}"


@asynccontextmanager
async def open_postgres_stores(
    settings: Settings, name: str
) -> AsyncIterator[tuple[PostgresApprovalStore, PostgresCaseStore]]:
    engine = create_engine(settings.postgres, application_name=name)
    try:
        factory = create_session_factory(engine)
        yield PostgresApprovalStore(factory), PostgresCaseStore(factory)
    finally:
        await engine.dispose()


@asynccontextmanager
async def open_specialist_deps(
    settings: Settings, *, knowledge: bool, name: str
) -> AsyncIterator[tuple[SpecialistDeps, CaseStore]]:
    """LLM por el gateway, ticketing por HTTP, aprobaciones y casos en PostgreSQL.

    `knowledge=False` no carga la base de conocimiento (el Creativo no la usa).
    """
    async with AsyncExitStack() as stack:
        approvals, cases = await stack.enter_async_context(open_postgres_stores(settings, name))
        ticketing = await stack.enter_async_context(
            HttpTicketingClient.from_settings(settings.ticketing_api)
        )
        retrieval = None
        if knowledge:
            runtime = await build_runtime(RetrievalSettings(), settings)
            stack.push_async_callback(runtime.aclose)
            retrieval = runtime.service
        shield = await stack.enter_async_context(build_shield(settings))
        deps = SpecialistDeps(
            llm=build_gateway(settings),
            ticketing=ticketing,
            retrieval=retrieval,
            # Cada caso es un hilo nuevo. Las preferencias, en memoria del proceso
            # (en la nube: AgentCore Memory / Cosmos DB, fases 13-14).
            preferences=InMemoryPreferenceStore(),
            checkpoints=InMemoryCheckpointStore(),
            approvals=approvals,
            send_reply=log_outbox,
            shield=shield,
            pii=build_pii_detector(settings),
            fail_open=settings.guardrails.fail_open,
        )
        yield deps, cases


@asynccontextmanager
async def open_remote_specialists(
    settings: Settings, specialists: Iterable[Specialist]
) -> AsyncIterator[dict[Specialist, SpecialistAgent]]:
    """Clientes A2A con el token de servicio del orquestador (emisor de desarrollo en local)."""
    config = settings.a2a
    issuer = DevTokenIssuer.load_or_create(Path(config.dev_private_key_file), issuer=config.issuer)
    remotes: dict[Specialist, SpecialistAgent] = {}
    async with AsyncExitStack() as stack:
        for specialist in specialists:
            auth = BearerAuth(dev_token_source(issuer, config, specialist.value))
            url = config.urls[specialist.value]
            http = await stack.enter_async_context(
                httpx.AsyncClient(base_url=url, auth=auth, timeout=config.timeout_s)
            )
            remote = await RemoteSpecialist.connect(specialist, url, http)
            stack.push_async_callback(remote.close)
            remotes[specialist] = remote
        yield remotes

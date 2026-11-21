"""Composición con adaptadores reales: la comparten el servidor A2A y los workers de la cola."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx

from booking_agent.guardrails import build_pii_detector, build_shield
from genaipocs_core.adapters.gcp_auth import google_id_token
from genaipocs_core.adapters.llm_factory import build_gateway
from genaipocs_core.adapters.memory import InMemoryCheckpointStore, InMemoryPreferenceStore
from genaipocs_core.adapters.online_eval_factory import build_agent_evaluator
from genaipocs_core.adapters.postgres import create_engine, create_session_factory
from genaipocs_core.adapters.postgres.approvals import PostgresApprovalStore
from genaipocs_core.adapters.postgres.cases import PostgresCaseStore
from genaipocs_core.adapters.postgres.outbox import PostgresOutboxStore
from genaipocs_core.adapters.ticketing_http import HttpTicketingClient
from genaipocs_core.application.agent_online_eval import AgentOnlineEvaluator
from genaipocs_core.application.orchestration import SpecialistAgent
from genaipocs_core.application.tools import ToolContext
from genaipocs_core.config import Settings
from genaipocs_core.domain import Specialist
from genaipocs_core.ports.cases import CaseStore
from genaipocs_mcp.auth import DevTokenIssuer
from harness.rules.case_reply import SendCaseReplyArgs
from orchestrator.remote import (
    BearerAuth,
    RemoteSpecialist,
    TokenSource,
    dev_token_source,
    google_id_token_source,
)
from orchestrator.settings import OrchestratorSettings
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
async def open_outbox(settings: Settings, name: str) -> AsyncIterator[PostgresOutboxStore]:
    engine = create_engine(settings.postgres, application_name=name)
    try:
        yield PostgresOutboxStore(create_session_factory(engine))
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
        evaluator = await stack.enter_async_context(running_agent_evaluator(settings))
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
            evaluator=evaluator,
        )
        yield deps, cases


@asynccontextmanager
async def running_agent_evaluator(
    settings: Settings, orchestrator: OrchestratorSettings | None = None
) -> AsyncIterator[AgentOnlineEvaluator | None]:
    """Juez online con su worker en segundo plano mientras viva el proceso. Al parar, lo que
    quede en la cola se descarta: es una muestra, no un trabajo pendiente."""
    config = orchestrator or OrchestratorSettings()
    evaluator = build_agent_evaluator(
        settings,
        sample_rate=config.online_eval_rate,
        max_per_minute=config.online_eval_per_minute,
    )
    if evaluator is None:
        yield None
        return
    stop = asyncio.Event()
    task = asyncio.create_task(evaluator.run(stop))
    try:
        yield evaluator
    finally:
        stop.set()
        await task


def a2a_token_source(settings: Settings, specialist: Specialist) -> TokenSource:
    """Token del orquestador para un especialista: dev (emisor local) o gcp (ID token)."""
    config = settings.a2a
    audience = config.audience(specialist.value)
    if config.identity == "gcp":
        gcp = settings.gcp
        return google_id_token_source(audience, lambda aud: google_id_token(gcp, aud))
    issuer = DevTokenIssuer.load_or_create(Path(config.dev_private_key_file), issuer=config.issuer)
    return dev_token_source(issuer, config, specialist.value)


@asynccontextmanager
async def open_remote_specialists(
    settings: Settings, specialists: Iterable[Specialist]
) -> AsyncIterator[dict[Specialist, SpecialistAgent]]:
    """Clientes A2A con el token de servicio del orquestador (emisor de desarrollo en local, ID
    token de Google con `A2A__IDENTITY=gcp`)."""
    config = settings.a2a
    remotes: dict[Specialist, SpecialistAgent] = {}
    async with AsyncExitStack() as stack:
        for specialist in specialists:
            auth = BearerAuth(a2a_token_source(settings, specialist))
            url = config.urls[specialist.value]
            http = await stack.enter_async_context(
                httpx.AsyncClient(base_url=url, auth=auth, timeout=config.timeout_s)
            )
            remote = await RemoteSpecialist.connect(specialist, url, http)
            stack.push_async_callback(remote.close)
            remotes[specialist] = remote
        yield remotes

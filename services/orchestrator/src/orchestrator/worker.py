"""Workers del flujo asíncrono. TIENE COSTE (cada caso llama al LLM).

Cada rol es un consumidor independiente: se escala con más procesos del rol con más cola
(competing consumers), igual que el indexer de la fase 3.

    docker compose up -d                               # PostgreSQL + RabbitMQ
    uv run python -m orchestrator.worker               # todos los roles en un proceso
    uv run python -m orchestrator.worker triage        # solo el triaje
    uv run python -m orchestrator.worker refunds knowledge
    uv run python -m orchestrator.worker relay         # solo el relay del outbox (fase 10.5)
    ORCHESTRATOR__SPECIALISTS=a2a uv run python -m orchestrator.worker   # especialistas A2A
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from contextlib import AsyncExitStack

from headache_core.adapters.messaging_factory import open_message_bus
from headache_core.adapters.otel import setup_observability, shutdown_telemetry
from headache_core.application.orchestration import SpecialistAgent, TriageAgent
from headache_core.application.outbox import OutboxRelay
from headache_core.config import get_settings
from headache_core.domain import Specialist
from orchestrator.async_flow import subscribe_workers
from orchestrator.bootstrap import open_outbox, open_remote_specialists, open_specialist_deps
from orchestrator.settings import OrchestratorSettings
from orchestrator.specialists import build_specialist

logger = logging.getLogger("orchestrator.worker")
ROLES = ("triage", *(s.value for s in Specialist), "relay")


async def run(roles: list[str]) -> None:
    settings = get_settings()
    setup_observability(settings, service_name="orchestrator_worker")
    config = OrchestratorSettings()
    wanted = [Specialist(r) for r in roles if r not in ("triage", "relay")]
    agents = [r for r in roles if r != "relay"]
    local = config.specialists == "inprocess"
    knowledge = local and any(s is not Specialist.CREATIVE for s in wanted)
    async with AsyncExitStack() as stack:
        bus = await stack.enter_async_context(
            open_message_bus(
                settings,
                config.broker,
                prefetch=config.prefetch,
                max_deliveries=config.max_deliveries,
                retry_delay_ms=config.retry_delay_ms,
            )
        )
        if agents:
            deps, cases = await stack.enter_async_context(
                open_specialist_deps(settings, knowledge=knowledge, name="orchestrator-worker")
            )
            specialists: dict[Specialist, SpecialistAgent] = (
                {s: build_specialist(s, deps) for s in wanted}
                if local
                else await stack.enter_async_context(open_remote_specialists(settings, wanted))
            )
            topics = await subscribe_workers(
                bus,
                cases,
                triage=TriageAgent(deps.llm) if "triage" in roles else None,
                specialists=specialists,
            )
            logger.info(
                "Escuchando %s en %s (especialistas %s).", topics, config.broker, config.specialists
            )
        if "relay" in roles:
            # Sin relay, los cambios de estado se guardan pero sus eventos esperan en el outbox.
            outbox = await stack.enter_async_context(open_outbox(settings, "orchestrator-relay"))
            relay = OutboxRelay(outbox, bus, batch_size=config.relay_batch_size)
            stop = asyncio.Event()
            task = asyncio.create_task(relay.run(stop, idle_seconds=config.relay_idle_seconds))
            stack.push_async_callback(lambda: task)
            stack.callback(stop.set)
            logger.info("Relay del outbox activo (lotes de %d).", config.relay_batch_size)
        logger.info("Ctrl+C para parar.")
        await asyncio.Event().wait()


def main() -> None:
    parser = argparse.ArgumentParser(description="Workers del flujo asíncrono de casos")
    parser.add_argument("roles", nargs="*", choices=ROLES, help="Por defecto, todos")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s pid=%(process)d %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("aio_pika", "aiormq", "httpx", "azure"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(run(args.roles or list(ROLES)))
    finally:
        shutdown_telemetry()


if __name__ == "__main__":
    main()

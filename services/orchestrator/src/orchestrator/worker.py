"""Workers del flujo asíncrono. TIENE COSTE (cada caso llama al LLM).

Cada rol es un consumidor independiente: se escala con más procesos del rol con más cola
(competing consumers), igual que el indexer de la fase 3.

    docker compose up -d                               # PostgreSQL + RabbitMQ
    uv run python -m orchestrator.worker               # todos los roles en un proceso
    uv run python -m orchestrator.worker triage        # solo el triaje
    uv run python -m orchestrator.worker refunds knowledge
    ORCHESTRATOR__SPECIALISTS=a2a uv run python -m orchestrator.worker   # especialistas A2A
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from contextlib import AsyncExitStack

from headache_core.adapters.messaging_factory import open_message_bus
from headache_core.application.orchestration import SpecialistAgent, TriageAgent
from headache_core.config import get_settings
from headache_core.domain import Specialist
from orchestrator.async_flow import subscribe_workers
from orchestrator.bootstrap import open_remote_specialists, open_specialist_deps
from orchestrator.settings import OrchestratorSettings
from orchestrator.specialists import build_specialist

logger = logging.getLogger("orchestrator.worker")
ROLES = ("triage", *(s.value for s in Specialist))


async def run(roles: list[str]) -> None:
    settings = get_settings()
    config = OrchestratorSettings()
    wanted = [Specialist(r) for r in roles if r != "triage"]
    local = config.specialists == "inprocess"
    knowledge = local and any(s is not Specialist.CREATIVE for s in wanted)
    async with AsyncExitStack() as stack:
        deps, cases = await stack.enter_async_context(
            open_specialist_deps(settings, knowledge=knowledge, name="orchestrator-worker")
        )
        specialists: dict[Specialist, SpecialistAgent] = (
            {s: build_specialist(s, deps) for s in wanted}
            if local
            else await stack.enter_async_context(open_remote_specialists(settings, wanted))
        )
        bus = await stack.enter_async_context(
            open_message_bus(
                settings,
                config.broker,
                prefetch=config.prefetch,
                max_deliveries=config.max_deliveries,
                retry_delay_ms=config.retry_delay_ms,
            )
        )
        topics = await subscribe_workers(
            bus,
            cases,
            triage=TriageAgent(deps.llm) if "triage" in roles else None,
            specialists=specialists,
        )
        logger.info(
            "Escuchando %s en %s (especialistas %s). Ctrl+C para parar.",
            topics,
            config.broker,
            config.specialists,
        )
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
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(args.roles or list(ROLES)))


if __name__ == "__main__":
    main()

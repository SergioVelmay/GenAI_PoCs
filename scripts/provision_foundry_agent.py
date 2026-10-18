"""Carril A en Azure: crea el agente del Knowledge & Booking Agent en Foundry Agent Service.

La definición sale del mismo `AgentSpec` que usan los cinco sabores (instrucciones, índice de
skills y esquemas de las tools), así que el agente servidor y los locales no se desalinean.

Uso:
    uv run python scripts/provision_foundry_agent.py            # dry-run: imprime la definición
    uv run python scripts/provision_foundry_agent.py --apply    # crea una versión (nube, con coste)
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from booking_agent.managed.foundry import agent_definition
from booking_agent.spec import build_booking_spec
from headache_core.adapters.foundry_iq import publish_agent
from headache_core.adapters.local_retrieval import LocalRetrieval
from headache_core.adapters.memory import (
    HashingEmbedding,
    InMemoryPreferenceStore,
    InMemoryUnitOfWork,
)
from headache_core.adapters.ticketing_local import UnitOfWorkTicketing
from headache_core.application.retrieval import RetrievalService
from headache_core.application.tools import build_ticketing_tools
from headache_core.config import Settings, get_settings

AGENT_NAME = "headache-booking-agent"


def definition(settings: Settings) -> dict[str, Any]:
    # Solo se necesitan los esquemas: las tools no se ejecutan al aprovisionar.
    uow = InMemoryUnitOfWork()
    spec = build_booking_spec(
        ticketing_tools=build_ticketing_tools(UnitOfWorkTicketing(lambda: uow)),
        retrieval=RetrievalService(LocalRetrieval([], embeddings=HashingEmbedding(64))),
        preferences=InMemoryPreferenceStore(),
    )
    return {
        "name": AGENT_NAME,
        "description": "Knowledge & Booking Agent de Headache (function tools en el cliente).",
        "definition": agent_definition(
            spec.system_prompt, spec.tools, model=settings.foundry_iq.agent_model
        ),
    }


async def apply(settings: Settings) -> None:
    import httpx
    from azure.identity.aio import DefaultAzureCredential

    endpoint = settings.foundry_iq.project_endpoint
    if not endpoint:
        raise SystemExit("Falta FOUNDRY_IQ__PROJECT_ENDPOINT.")
    async with DefaultAzureCredential() as credential, httpx.AsyncClient(timeout=60) as http:
        token = await credential.get_token("https://ai.azure.com/.default")
        version = await publish_agent(http, endpoint, token.token, definition(settings))
    print(f"Agente '{AGENT_NAME}' versión {version} publicado.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if args.apply:
        asyncio.run(apply(settings))
        return
    print(json.dumps(definition(settings), indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()

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
from contextlib import asynccontextmanager

import uvicorn
from starlette.applications import Starlette

from headache_core.adapters.otel import setup_observability, traced_asgi
from headache_core.config import Settings, get_settings
from headache_core.domain import Specialist
from orchestrator.bootstrap import open_specialist_deps
from orchestrator.remote import build_verifier, create_specialist_app
from orchestrator.specialists import build_specialist


@asynccontextmanager
async def specialist_app(
    specialist: Specialist, settings: Settings, public_url: str
) -> AsyncIterator[Starlette]:
    verifier = build_verifier(settings.a2a, specialist.value)
    knowledge = specialist is not Specialist.CREATIVE
    async with open_specialist_deps(
        settings, knowledge=knowledge, name=f"agent-{specialist.value}"
    ) as (deps, _):
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
    setup_observability(settings, service_name=f"agent_{specialist.value}")
    public_url = args.public_url or f"http://127.0.0.1:{args.port}"
    async with specialist_app(specialist, settings, public_url) as app:
        server = uvicorn.Server(
            uvicorn.Config(traced_asgi(app, settings), host=args.host, port=args.port)
        )
        await server.serve()


if __name__ == "__main__":
    asyncio.run(main())

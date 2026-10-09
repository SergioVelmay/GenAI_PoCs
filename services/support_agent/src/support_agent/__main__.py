"""Agente de soporte contra los tres servidores MCP. TIENE COSTE (llama al LLM).

Requiere la API de ticketing y los servidores MCP levantados. Con MCP__AUTH=jwt, el agente pide
un token por servidor (audience y scopes propios) al emisor de desarrollo, en nombre del cliente:

    $env:MCP__AUTH="jwt"
    uv run python -m support_agent --customer cus_0121338 "¿Qué pedidos tengo?"

En la nube el token lo emite Entra ID (On-Behalf-Of) o Cognito a partir del token del usuario.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastmcp import Client

from headache_core.adapters.llm_factory import build_gateway, build_tracer
from headache_core.adapters.memory import RecordingTracer
from headache_core.application.llm import CompositeTracer
from headache_core.application.tools import ToolContext
from headache_core.config import Settings, get_settings
from headache_mcp.auth import SERVER_SCOPES, DevTokenIssuer
from support_agent.agent import SupportAgent
from support_agent.toolset import McpToolset


def _clients(settings: Settings, customer: str) -> dict[str, Client[Any]]:
    if settings.mcp.auth != "jwt":
        return {name: Client(url) for name, url in settings.mcp.servers.items()}
    if settings.environment != "local":
        raise SystemExit(
            "El emisor de desarrollo solo existe en local; en la nube usa OBO/Cognito."
        )
    issuer = DevTokenIssuer.load_or_create(
        Path(settings.mcp.dev_private_key_file), issuer=settings.mcp.issuer
    )
    return {
        name: Client(
            url,
            auth=issuer.mint(
                subject=customer,
                audience=settings.mcp.audience(name),
                scopes=SERVER_SCOPES[name],
            ),
        )
        for name, url in settings.mcp.servers.items()
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Agente mínimo con tools MCP")
    parser.add_argument("question")
    parser.add_argument("--customer", required=True, help="Cliente autenticado")
    args = parser.parse_args()

    settings = get_settings()
    ledger = RecordingTracer()
    tracer = build_tracer(settings)
    llm = build_gateway(settings, tracer=CompositeTracer([tracer, ledger]))

    async with McpToolset(_clients(settings, args.customer)) as tools:
        print(f"Tools descubiertas: {', '.join(tools.names)}\n")
        result = await SupportAgent(llm, tools).answer(
            args.question, ToolContext(customer_id=args.customer)
        )

    for execution in result.executions:
        print(f"  {execution.call.name}({execution.call.arguments}) -> {execution.error or 'ok'}")
        print(f"     {execution.output[:200]}")
    print(f"\nRespuesta ({result.stop_reason}):\n{result.answer}\n")
    cost = sum((r.cost_usd or Decimal(0) for r in ledger.records), Decimal(0))
    print(f"Llamadas al modelo: {len(result.responses)}  coste: ${cost:.6f}")
    flush = getattr(tracer, "flush", None)
    if callable(flush):
        flush()


if __name__ == "__main__":
    asyncio.run(main())

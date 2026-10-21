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

from headache_core.adapters.llm_factory import build_gateway, build_tracer
from headache_core.adapters.memory import RecordingTracer
from headache_core.adapters.otel import setup_observability, shutdown_telemetry
from headache_core.application.llm import CompositeTracer
from headache_core.application.telemetry import genai
from headache_core.application.tools import ToolContext
from headache_core.config import get_settings
from support_agent.agent import SupportAgent
from support_agent.toolset import McpToolset, mcp_clients


async def main() -> None:
    parser = argparse.ArgumentParser(description="Agente mínimo con tools MCP")
    parser.add_argument("question")
    parser.add_argument("--customer", required=True, help="Cliente autenticado")
    parser.add_argument("--gateway", action="store_true", help="Usar el MCP Gateway (8100)")
    args = parser.parse_args()

    settings = get_settings()
    telemetry = setup_observability(settings, service_name="support_agent")
    ledger = RecordingTracer()
    tracer = build_tracer(settings)
    llm = build_gateway(settings, tracer=CompositeTracer([tracer, ledger]))

    # Un span raíz: las llamadas al modelo, a las tools y a MCP cuelgan de la misma traza.
    with telemetry.span(
        genai.span_name(genai.GenAIOperation.INVOKE_AGENT, "support_agent"),
        attributes={genai.OPERATION_NAME: genai.GenAIOperation.INVOKE_AGENT.value},
    ):
        async with McpToolset(mcp_clients(settings, args.customer, gateway=args.gateway)) as tools:
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
    shutdown_telemetry()


if __name__ == "__main__":
    asyncio.run(main())

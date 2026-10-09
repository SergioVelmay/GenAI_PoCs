"""Bucle de tools hecho a mano (carril B) contra la API de ticketing real. TIENE COSTE.

Requiere PostgreSQL con datos (fase 1) y la API levantada:
    uv run uvicorn ticketing_api.app:create_app --factory --host 127.0.0.1 --port 8000

    uv run python scripts/tool_loop_demo.py --customer cus_0000001 "¿Qué pedidos tengo?"
    $env:LLM_PROVIDER="aws"; uv run python scripts/tool_loop_demo.py --customer cus_0000001 `
        "Quiero el reembolso de mi último pedido, no puedo ir"
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from headache_core.adapters.llm_factory import build_gateway, build_tracer
from headache_core.adapters.memory import RecordingTracer
from headache_core.adapters.ticketing_http import HttpTicketingClient
from headache_core.application.llm import CompositeTracer
from headache_core.application.tools import (
    TICKETING_AGENT_PROMPT,
    ToolContext,
    ToolLoop,
    build_ticketing_tools,
)
from headache_core.config import get_settings
from headache_core.ports import ChatMessage, Role


async def main() -> None:
    parser = argparse.ArgumentParser(description="Agente mínimo con tool calling manual")
    parser.add_argument("question")
    parser.add_argument("--customer", required=True, help="Cliente autenticado (ToolContext)")
    parser.add_argument("--max-iterations", type=int, default=5)
    args = parser.parse_args()

    settings = get_settings()
    ledger = RecordingTracer()
    tracer = build_tracer(settings)
    llm = build_gateway(settings, tracer=CompositeTracer([tracer, ledger]))
    async with HttpTicketingClient.from_settings(settings.ticketing_api) as ticketing:
        loop = ToolLoop(llm, build_ticketing_tools(ticketing), max_iterations=args.max_iterations)
        result = await loop.run(
            [
                ChatMessage(Role.SYSTEM, TICKETING_AGENT_PROMPT, cache_checkpoint=True),
                ChatMessage(Role.USER, args.question),
            ],
            ToolContext(customer_id=args.customer),
        )

    print(f"Proveedor={settings.llm_provider}  motor={settings.llm_gateway.engine}\n")
    for execution in result.executions:
        status = execution.error or "ok"
        print(
            f"  tool {execution.call.name}({execution.call.arguments}) -> {status} "
            f"[{execution.latency_ms:.0f} ms]"
        )
        print(f"       {execution.output[:200]}")
    print(f"\nRespuesta ({result.stop_reason}):\n{result.answer}\n")

    total = Decimal(0)
    for record in ledger.records:
        if record.response is None:
            print(f"  llamada fallida: {record.error!r}")
            continue
        usage = record.response.usage
        cost = record.cost_usd or Decimal(0)
        total += cost
        print(
            f"  {record.response.model:<40} in={usage.input_tokens:>6} "
            f"(cache {usage.cached_input_tokens:>5}) out={usage.output_tokens:>4} "
            f"tools={len(record.response.tool_calls)}  ${cost:.6f}"
        )
    print(f"\nLlamadas al modelo: {len(result.responses)}  coste total: ${total:.6f}")
    flush = getattr(tracer, "flush", None)
    if callable(flush):
        flush()


if __name__ == "__main__":
    asyncio.run(main())

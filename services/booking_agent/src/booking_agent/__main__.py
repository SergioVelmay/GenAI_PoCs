"""Knowledge & Booking Agent por consola. TIENE COSTE (llama al LLM).

Requiere la API de ticketing y los servidores MCP levantados (como `python -m support_agent`).
El conocimiento sale del servicio de retrieval en proceso (local por defecto, RETRIEVAL__*).

    uv run python -m booking_agent --flavor native --customer cus_0121338 "¿Qué hay en Madrid?"
    uv run python -m booking_agent --customer cus_0121338          # modo interactivo (hilo)

La memoria (hilos y preferencias) vive en el proceso: en modo interactivo se ve el recorte, el
resumen y las preferencias entre turnos. Los reembolsos quedan pendientes de aprobación humana:
`/pendientes`, `/aprobar <id>` y `/rechazar <id>` hacen de revisor en la misma consola.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from booking_agent.flavors import FLAVORS, create_agent
from booking_agent.guardrails import (
    build_pii_detector,
    build_shield,
    guarded_agent,
    guarded_executor,
)
from booking_agent.runtime import AgentDeps
from booking_agent.spec import build_booking_spec
from harness import ApprovalError, ApprovalService
from headache_core.adapters.llm_factory import build_gateway, build_tracer
from headache_core.adapters.memory import (
    InMemoryApprovalStore,
    InMemoryCheckpointStore,
    InMemoryPreferenceStore,
    RecordingTracer,
)
from headache_core.application.llm import CompositeTracer
from headache_core.application.tools import ToolContext
from headache_core.config import get_settings
from retrieval.bootstrap import build_runtime
from retrieval.settings import RetrievalSettings
from support_agent.toolset import McpToolset, mcp_clients

REVIEWER = "ops-cli"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge & Booking Agent")
    parser.add_argument("question", nargs="?", help="Sin pregunta: modo interactivo")
    parser.add_argument("--customer", required=True, help="Cliente autenticado")
    parser.add_argument("--flavor", choices=sorted(FLAVORS), default="native")
    parser.add_argument("--thread", default="cli")
    parser.add_argument("--gateway", action="store_true", help="Tools por el MCP Gateway")
    args = parser.parse_args()

    settings = get_settings()
    ledger = RecordingTracer()
    tracer = build_tracer(settings)
    llm = build_gateway(settings, tracer=CompositeTracer([tracer, ledger]))
    knowledge = await build_runtime(RetrievalSettings(), settings)
    preferences = InMemoryPreferenceStore()
    context = ToolContext(customer_id=args.customer)
    try:
        clients = mcp_clients(settings, args.customer, gateway=args.gateway)
        fail_open = settings.guardrails.fail_open
        async with McpToolset(clients) as ticketing, build_shield(settings) as shield:
            spec = build_booking_spec(
                ticketing_tools=ticketing, retrieval=knowledge.service, preferences=preferences
            )
            approvals = InMemoryApprovalStore()
            reviewer = ApprovalService(approvals, spec.tools)
            executor = guarded_executor(spec, approvals, shield, fail_open=fail_open)
            agent = guarded_agent(
                create_agent(
                    args.flavor,
                    AgentDeps(spec, llm, executor, InMemoryCheckpointStore(), preferences),
                ),
                shield=shield,
                pii=build_pii_detector(settings),
                fail_open=fail_open,
            )
            questions = [args.question] if args.question else None
            while True:
                if questions:
                    question = questions.pop()
                else:
                    question = (await asyncio.to_thread(input, "\ncliente> ")).strip()
                if not question or question in ("salir", "exit"):
                    break
                if question.startswith("/"):
                    await review(reviewer, question)
                    continue
                seen = len(executor.journal)
                turn = await agent.respond(args.thread, question, context)
                for execution in executor.journal[seen:]:
                    call = execution.call
                    print(f"  {call.name}({call.arguments}) -> {execution.error or 'ok'}")
                note = " [memoria compactada]" if turn.compacted else ""
                if turn.guardrails:
                    note += f" [harness: {', '.join(turn.guardrails)}]"
                print(f"\n{agent.flavor} ({turn.stop_reason}){note}:\n{turn.answer}")
                if questions is not None:
                    break
    finally:
        await knowledge.aclose()
    cost = sum((r.cost_usd or Decimal(0) for r in ledger.records), Decimal(0))
    print(f"\nLlamadas al modelo: {len(ledger.records)}  coste: ${cost:.6f}")
    flush = getattr(tracer, "flush", None)
    if callable(flush):
        flush()


async def review(service: ApprovalService, command: str) -> None:
    """Consola del revisor (simula al equipo de operaciones en el mismo proceso)."""
    name, *rest = command.split(maxsplit=2)
    if name == "/pendientes":
        for request in await service.pending():
            expires = f"{request.expires_at:%Y-%m-%d %H:%M}"
            print(f"  {request.id} [{request.tool}] {request.summary} (caduca {expires} UTC)")
        return
    if name in ("/aprobar", "/rechazar") and rest:
        note = rest[1] if len(rest) > 1 else ""
        try:
            decided = await service.decide(
                rest[0], reviewer=REVIEWER, approve=name == "/aprobar", note=note
            )
        except ApprovalError as exc:
            print(f"  {exc}")
            return
        print(
            f"  {decided.id}: {decided.status}"
            + (f" -> {decided.result}" if decided.result else "")
        )
        return
    print("  Comandos: /pendientes | /aprobar <id> [nota] | /rechazar <id> [nota]")


if __name__ == "__main__":
    asyncio.run(main())

"""Tráfico sintético con telemetría real, sin coste (fase 11.5).

Genera, con modelos falsos (precio de gpt-4.1-mini para que haya coste estimado):
- un caso asíncrono completo: alta → outbox → relay → cola → triaje → especialista (tools,
  aprobación) → resultado. Imprime el enlace a su traza en Jaeger;
- preguntas al edge de varios planes (aciertos de caché, rechazos 429, streaming);
- llamadas al modelo de varios agentes, con algún 429 del proveedor.

Requiere el collector (perfil telemetry):

    docker compose --profile telemetry up -d
    uv run python scripts/telemetry_demo.py --rounds 5
    # Jaeger http://127.0.0.1:16686 · Grafana http://127.0.0.1:3001 · Prometheus :9095
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
from dataclasses import replace

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from edge_api.testing import client, token
from headache_core.adapters.memory import (
    FakeLLM,
    HashingEmbedding,
    InMemoryCaseIntakeStore,
    InMemoryCaseStore,
    InMemoryMessageBus,
    InMemoryOutbox,
    InMemorySemanticCache,
)
from headache_core.adapters.otel import setup_observability, shutdown_telemetry
from headache_core.application.case_intake import CaseIntakeService, CaseRequest
from headache_core.application.llm import ObservedLLM, PriceTable
from headache_core.application.orchestration import TriageAgent
from headache_core.application.outbox import OutboxRelay
from headache_core.application.semantic_cache import SemanticCache
from headache_core.application.telemetry import TelemetryLLMTracer, agent_scope
from headache_core.application.telemetry.messaging import TracedMessageBus
from headache_core.config import Settings, TelemetrySettings
from headache_core.ports import ChatMessage, LLMRateLimitError, Role, ToolCall
from orchestrator.async_flow import subscribe_workers
from orchestrator.specialists import build_specialists
from orchestrator.testing import CUSTOMER, ORDER, local_world

MODEL = "gpt-4.1-mini"
PENDING = "Tu solicitud queda pendiente de revisión por un agente de Headache."
QUESTIONS = [
    "¿Hasta cuándo puedo pedir un reembolso?",
    "¿A qué hora abren las puertas del recinto?",
    "¿Se puede entrar con carrito de bebé?",
    "¿Hay descuento para grupos?",
]


def _observed(llm: FakeLLM, tier: str) -> ObservedLLM:
    return ObservedLLM(
        llm,
        tracer=TelemetryLLMTracer(),
        prices=PriceTable(),
        name=f"simulated:{tier}",
        metadata={"provider": "simulated", "tier": tier},
    )


async def async_case(index: int) -> str:
    """Un caso de reembolso de punta a punta. Devuelve el trace_id."""
    triage = json.dumps(
        {
            "intent": "refund_request",
            "language": "es",
            "order_id": ORDER,
            "urgency": "normal",
            "summary": "Pide el reembolso.",
        }
    )
    refund = [ToolCall("c1", "request_refund", json.dumps({"order_id": ORDER, "reason": "Viaje."}))]
    world = local_world([triage, refund, PENDING])
    world.llm._model = MODEL  # El fake responde como el modelo barato: así hay coste estimado.
    llm = _observed(world.llm, "cheap")
    outbox = InMemoryOutbox()
    bus = TracedMessageBus(InMemoryMessageBus(), system="rabbitmq")
    cases = InMemoryCaseStore(outbox)
    await subscribe_workers(
        bus,
        cases,
        triage=TriageAgent(llm),
        specialists=build_specialists(replace(world.deps, llm=llm)),
    )
    tracer = trace.get_tracer("telemetry_demo")
    with tracer.start_as_current_span("POST /cases", kind=trace.SpanKind.SERVER) as span:
        span.set_attribute("http.route", "/cases")
        await CaseIntakeService(InMemoryCaseIntakeStore(outbox)).submit(
            customer_id=CUSTOMER,
            idempotency_key=f"demo-case-{index:04d}-{random.randrange(10**6)}",
            request=CaseRequest("Reembolso", "No puedo ir al concierto, devolvedme el dinero."),
        )
        trace_id = trace.format_trace_id(span.get_span_context().trace_id)
    relay = OutboxRelay(outbox, bus)
    for _ in range(5):
        if not await relay.drain():
            break
    await relay.report_backlog()
    return trace_id


async def edge_traffic(rounds: int) -> None:
    cache = SemanticCache(InMemorySemanticCache(), HashingEmbedding(256), threshold=0.9)
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider), "setup_observability debe ir antes"
    async with client(cache=cache, tracer_provider=provider) as http:
        public = {"Authorization": f"Bearer {token('usr_demo', plan='public')}"}
        internal = {"Authorization": f"Bearer {token('ops_demo', plan='internal')}"}
        for _ in range(rounds):
            for question in QUESTIONS:
                await http.post("/ask", json={"question": question}, headers=internal)
                await http.post("/ask/stream", json={"question": question}, headers=public)
            # Anónimo insistente: 2 por minuto, el resto 429.
            for _ in range(4):
                await http.post("/ask", json={"question": QUESTIONS[0]})


async def agent_calls(rounds: int) -> None:
    for agent, tier in (("refunds", "strong"), ("knowledge", "cheap"), ("creative", "cheap")):
        script: list[str | Exception] = ["respuesta " * random.randint(20, 120)] * rounds
        if agent == "knowledge":
            script.append(LLMRateLimitError("429 del proveedor", retry_after=2))
        llm = _observed(FakeLLM(script, model=MODEL), tier)
        with agent_scope(agent):
            for _ in script:
                with contextlib.suppress(LLMRateLimitError):
                    await llm.complete([ChatMessage(Role.USER, "contexto " * 400)])


async def main() -> None:
    parser = argparse.ArgumentParser(description="Tráfico sintético con telemetría (coste 0)")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument(
        "--minutes",
        type=float,
        default=0.0,
        help="Repetir el tráfico durante este tiempo (para ver curvas en Grafana)",
    )
    parser.add_argument("--endpoint", default="http://127.0.0.1:4318")
    args = parser.parse_args()
    settings = Settings(
        telemetry=TelemetrySettings(
            exporter="otlp", otlp_endpoint=args.endpoint, metrics_interval_seconds=5
        )
    )
    setup_observability(settings, service_name="telemetry_demo")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + args.minutes * 60
    trace_ids: list[str] = []
    while True:
        trace_ids += [await async_case(len(trace_ids) + i) for i in range(args.rounds)]
        await edge_traffic(args.rounds)
        await agent_calls(args.rounds)
        if loop.time() >= deadline:
            break
        await asyncio.sleep(random.uniform(1, 4))
    shutdown_telemetry()
    print(f"Casos asíncronos: {len(trace_ids)}. Traza del primero:")
    print(f"  http://127.0.0.1:16686/trace/{trace_ids[0]}")
    print("Grafana: http://127.0.0.1:3001 (dashboard Headache · GenAI)")


if __name__ == "__main__":
    asyncio.run(main())

"""Contexto en una conversación larga: nuestra compactación frente a Foundry Agent Service.

El mismo hilo (un dato personal en el turno 1, doce preguntas de conocimiento y una pregunta
final sobre el dato) se ejecuta con:

- `native`: historial en nuestro checkpoint store, compactado con `ContextPolicy` por defecto
  (resumen al pasar de 6.000 tokens estimados).
- `foundry`: historial en una `conversation` de Foundry Agent Service; el servicio solo trunca
  al acercarse a la ventana del modelo (1 M de tokens en gpt-4.1-mini).

Mide los tokens de entrada por turno (lo que se factura), el coste y si el dato del turno 1
sobrevive. TIENE COSTE (~0,05 $) y exige el agente publicado (provision_foundry_agent --apply).

    uv run python scripts/context_growth_demo.py
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from booking_agent.flavors import create_agent
from booking_agent.flavors.foundry import create_cloud
from booking_agent.managed.foundry import AgentServiceClient, FoundryAgentService, ServiceReply
from booking_agent.runtime import AgentDeps, JournaledExecutor
from booking_agent.spec import build_booking_spec
from headache_core.adapters.llm_factory import build_gateway
from headache_core.adapters.memory import (
    InMemoryCheckpointStore,
    InMemoryPreferenceStore,
    RecordingTracer,
)
from headache_core.adapters.ticketing_local import UnitOfWorkTicketing
from headache_core.application.agents.runtime import AgentRuntime
from headache_core.application.llm.pricing import PriceTable
from headache_core.application.tools import ToolContext, build_ticketing_tools
from headache_core.config import get_settings
from headache_core.ports import LLMPort, TokenUsage
from rag_evals.agents.suite import build_knowledge
from rag_evals.agents.world import NOW, build_world

CUSTOMER = "cus_0000001"
FACT = "Me llamo Ana y en noviembre viajo a Lisboa. Solo quería que lo supieras."
QUESTIONS = [
    f"¿Con cuánta antelación abren las puertas en {city}?"
    for city in ("Madrid", "Barcelona", "Paris", "London", "Lisbon", "Milan")
] + [
    f"¿Hasta cuándo puedo pedir el reembolso de una entrada de {category}?"
    for category in (
        "Conciertos a la luz de las velas",
        "Teatro y espectáculos",
        "Gastronomía y bebidas",
        "Tours y visitas",
        "Deportes y actividades",
        "Ocio nocturno",
    )
]
RECALL = "¿Cómo me llamo y a qué ciudad te dije que viajaba?"
MODEL = "gpt-4.1-mini"
OUT = Path("evals/reports/azure/context-growth.md")


@dataclass
class UsageRecordingService:
    """Envuelve el servicio real y anota los tokens de cada respuesta."""

    inner: AgentServiceClient
    usages: list[TokenUsage] = field(default_factory=list)

    async def create_conversation(self) -> str:
        return await self.inner.create_conversation()

    async def append(self, conversation_id: str, items: Sequence[dict[str, object]]) -> None:
        await self.inner.append(conversation_id, items)

    async def respond(
        self, conversation_id: str, items: Sequence[dict[str, object]]
    ) -> ServiceReply:
        reply = await self.inner.respond(conversation_id, items)
        if reply.usage is not None:
            self.usages.append(reply.usage)
        return reply


@dataclass
class Run:
    flavor: str
    per_turn_input: list[int] = field(default_factory=list)
    calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    compactions: int = 0
    final_answer: str = ""

    @property
    def cost(self) -> Decimal:
        usage = TokenUsage(
            self.input_tokens, self.output_tokens, cached_input_tokens=self.cached_tokens
        )
        return PriceTable().cost(MODEL, usage) or Decimal(0)

    @property
    def remembers(self) -> bool:
        answer = self.final_answer.casefold()
        return "ana" in answer and ("lisboa" in answer or "lisbon" in answer)


async def converse(
    agent: AgentRuntime, usages: Callable[[], Sequence[TokenUsage]], run: Run
) -> None:
    context = ToolContext(customer_id=CUSTOMER, request_id="context-growth")
    for text in [FACT, *QUESTIONS, RECALL]:
        before = len(usages())
        turn = await agent.respond("hilo-largo", text, context)
        turn_usages = usages()[before:]
        run.per_turn_input.append(sum(u.input_tokens for u in turn_usages))
        run.calls += len(turn_usages)
        run.input_tokens += sum(u.input_tokens for u in turn_usages)
        run.cached_tokens += sum(u.cached_input_tokens for u in turn_usages)
        run.output_tokens += sum(u.output_tokens for u in turn_usages)
        run.compactions += turn.compacted
        run.final_answer = turn.answer


def render(runs: Sequence[Run]) -> str:
    turns = len(runs[0].per_turn_input)
    lines = [
        "# Conversación larga: compactación propia frente a Foundry Agent Service",
        "",
        f"- {turns} turnos: dato personal, {len(QUESTIONS)} preguntas de conocimiento y la "
        "pregunta final sobre el dato. Modelo `gpt-4.1-mini` en ambos.",
        "",
        "| Sabor | Llamadas | Tokens entrada | Cacheados | Tokens salida | Compactaciones "
        "| Coste USD | Recuerda el dato |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for run in runs:
        lines.append(
            f"| {run.flavor} | {run.calls} | {run.input_tokens} | {run.cached_tokens} | "
            f"{run.output_tokens} | {run.compactions} | {run.cost:.4f} | "
            f"{'sí' if run.remembers else 'no'} |"
        )
    lines += [
        "",
        "## Tokens de entrada por turno",
        "",
        "| Turno | " + " | ".join(run.flavor for run in runs) + " |",
        "|---:|" + "---:|" * len(runs),
    ]
    for index in range(turns):
        lines.append(
            f"| {index + 1} | " + " | ".join(str(run.per_turn_input[index]) for run in runs) + " |"
        )
    lines += ["", "## Respuesta final", ""]
    lines += [f"- **{run.flavor}**: {run.final_answer}" for run in runs]
    return "\n".join(lines) + "\n"


async def main() -> str:
    from azure.identity.aio import DefaultAzureCredential

    settings = get_settings()
    retrieval, _ = await build_knowledge()

    def deps(llm: LLMPort) -> AgentDeps:
        world = build_world()
        ticketing = UnitOfWorkTicketing(lambda: world, clock=lambda: NOW)
        preferences = InMemoryPreferenceStore()
        spec = build_booking_spec(
            ticketing_tools=build_ticketing_tools(ticketing),
            retrieval=retrieval,
            preferences=preferences,
        )
        return AgentDeps(
            spec, llm, JournaledExecutor(spec.tools), InMemoryCheckpointStore(), preferences
        )

    ledger = RecordingTracer()
    native = Run("native (compactación propia)")
    gateway = build_gateway(settings, tracer=ledger)

    def native_usages() -> list[TokenUsage]:
        # Incluye las llamadas del resumidor: también se facturan.
        return [r.response.usage for r in ledger.records if r.response is not None]

    await converse(create_agent("native", deps(gateway)), native_usages, native)

    foundry = Run("foundry (conversation del servicio)")
    async with DefaultAzureCredential() as credential:
        real = FoundryAgentService.from_project(settings.foundry_iq.project_endpoint, credential)
        service = UsageRecordingService(real)
        try:
            await converse(create_cloud(deps(gateway), service), lambda: service.usages, foundry)
        finally:
            await real.close()

    report = render([native, foundry])
    print(report)
    print(json.dumps({"native_cost": str(native.cost), "foundry_cost": str(foundry.cost)}))
    return report


if __name__ == "__main__":
    output = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(output, encoding="utf-8", newline="\n")

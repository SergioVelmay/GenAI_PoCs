"""Fase 11.4: métricas RED y de GenAI, y la aritmética de los SLO."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Sequence

import pytest
from pydantic import BaseModel, ConfigDict

from headache_core.adapters.memory import (
    FakeLLM,
    HashingEmbedding,
    InMemoryMessageBus,
    InMemoryOutbox,
    InMemoryRateLimiter,
    InMemorySemanticCache,
)
from headache_core.adapters.otel import RecordedTelemetry, recording_telemetry
from headache_core.application.admission import (
    AdmissionController,
    CallerRateLimitedError,
    plan_rules,
)
from headache_core.application.agents.runtime import AgentTurn
from headache_core.application.agents.tracing import TracedAgent
from headache_core.application.llm import (
    CircuitBreakerLLM,
    ConcurrencyLimitedLLM,
    ObservedLLM,
    PriceTable,
)
from headache_core.application.llm.streaming import stream_from
from headache_core.application.outbox import OutboxRelay
from headache_core.application.semantic_cache import SemanticCache
from headache_core.application.telemetry import TelemetryLLMTracer, genai, metrics
from headache_core.application.telemetry.messaging import TracedMessageBus
from headache_core.application.tools import (
    Tool,
    ToolContext,
    ToolLoop,
    ToolRegistry,
)
from headache_core.domain.admission import Caller, ClientPlan
from headache_core.domain.semantic_cache import CacheScope
from headache_core.domain.slo import (
    BURN_RATE_ALERTS,
    COST_BUDGET,
    SLOS,
    Severity,
    SliKind,
    Slo,
)
from headache_core.ports import (
    ChatMessage,
    LLMOverloadedError,
    LLMRateLimitError,
    LLMResponse,
    Message,
    ResponseSchema,
    Role,
    ToolCall,
    ToolConfig,
)
from headache_core.ports.outbox import OutboxEvent

HELLO = [ChatMessage(Role.USER, "hola")]


def _observed(llm: FakeLLM, *, tier: str = "cheap") -> ObservedLLM:
    return ObservedLLM(
        llm,
        tracer=TelemetryLLMTracer(),
        prices=PriceTable(),
        name=f"azure:{tier}",
        metadata={"provider": "azure", "tier": tier},
    )


async def test_llm_calls_record_duration_tokens_and_cost() -> None:
    with recording_telemetry() as recorded:
        llm = _observed(
            FakeLLM(["una respuesta corta", LLMRateLimitError("429")], model="gpt-4.1-mini")
        )
        await llm.complete(HELLO)
        with pytest.raises(LLMRateLimitError):
            await llm.complete(HELLO)

    assert recorded.total(metrics.LLM_DURATION) == 2
    assert recorded.total(metrics.LLM_DURATION, **{genai.ERROR_TYPE: "LLMRateLimitError"}) == 1
    ok = {genai.RESPONSE_MODEL: "gpt-4.1-mini", genai.HEADACHE_TIER: "cheap"}
    assert recorded.total(metrics.LLM_INPUT_TOKENS, **ok) > 0
    assert recorded.total(metrics.LLM_OUTPUT_TOKENS, **ok) > 0
    assert recorded.total(metrics.LLM_COST, **ok) > 0
    # Sin agente activo, el coste va a "none" (el asistente del edge, scripts).
    assert recorded.total(metrics.LLM_COST, **{genai.AGENT_NAME: "none"}) > 0


async def test_ttft_is_recorded_for_streams() -> None:
    with recording_telemetry() as recorded:
        [_ async for _ in stream_from(_observed(FakeLLM(["hola mundo"])), HELLO)]

    assert recorded.total(metrics.LLM_TTFT) == 1


class Gated:
    """Modelo que no responde hasta que se abre la puerta: ocupa el hueco del bulkhead."""

    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate
        self._inner = FakeLLM(["a"])

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        await self._gate.wait()
        return await self._inner.complete(messages)


async def test_client_rejections_are_counted_by_reason() -> None:
    clock = [0.0]
    breaker = CircuitBreakerLLM(
        FakeLLM([LLMRateLimitError("429", retry_after=30)]),
        name="azure:cheap",
        failure_threshold=1,
        reset_timeout=30,
        clock=lambda: clock[0],
    )
    with recording_telemetry() as recorded:
        with pytest.raises(LLMRateLimitError):
            await breaker.complete(HELLO)
        with pytest.raises(LLMOverloadedError):
            await breaker.complete(HELLO)
        gate = asyncio.Event()
        bulkhead = ConcurrencyLimitedLLM(Gated(gate), max_concurrency=1, max_wait=0, name="x")
        first = asyncio.create_task(bulkhead.complete(HELLO))
        await asyncio.sleep(0)
        with pytest.raises(LLMOverloadedError):
            await bulkhead.complete(HELLO)
        gate.set()
        await first

    reasons = {
        attributes["headache.llm.reject_reason"]: value
        for attributes, value in recorded.points(metrics.LLM_REJECTIONS)
    }
    assert reasons == {"circuit_open": 1, "bulkhead": 1}


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


async def _echo(args: EchoArgs, context: ToolContext) -> str:
    return args.text


class LoopAgent:
    flavor = "native"

    def __init__(self, llm: ObservedLLM) -> None:
        self._loop = ToolLoop(llm, ToolRegistry([Tool("echo", "Eco.", EchoArgs, _echo)]))

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        result = await self._loop.run([ChatMessage(Role.USER, message)], context)
        return AgentTurn(result.answer, result.stop_reason, guardrails=("pii_redacted",))


async def test_cost_tools_and_turns_are_attributed_to_the_agent() -> None:
    calls = [ToolCall("c1", "echo", json.dumps({"text": "x"})), ToolCall("c2", "inventada", "{}")]
    llm = FakeLLM([calls, "Listo."], model="gpt-4.1-mini")
    agent = TracedAgent(LoopAgent(_observed(llm)), name="booking")

    with recording_telemetry() as recorded:
        await agent.respond("t1", "hola", ToolContext(customer_id="cus_1"))

    booking = {genai.AGENT_NAME: "booking"}
    assert recorded.total(metrics.LLM_COST, **booking) > 0
    assert recorded.total(metrics.LLM_COST, **{genai.AGENT_NAME: "none"}) == 0
    assert recorded.total(metrics.TOOL_DURATION, **booking, **{genai.TOOL_NAME: "echo"}) == 1
    # Un nombre inventado por el modelo no crea una serie nueva: va a "unknown".
    unknown = {genai.TOOL_NAME: "unknown", genai.ERROR_TYPE: "unknown_tool"}
    assert recorded.total(metrics.TOOL_DURATION, **unknown) == 1
    assert (
        recorded.total(metrics.AGENT_DURATION, **booking, **{genai.HEADACHE_STOP_REASON: "answer"})
        == 1
    )
    assert (
        recorded.total(metrics.GUARDRAIL_TRIGGERS, **{"headache.guardrail.code": "pii_redacted"})
        == 1
    )


async def test_admission_decisions_by_route_plan_and_outcome() -> None:
    controller = AdmissionController(
        InMemoryRateLimiter(), rules=plan_rules({ClientPlan.PUBLIC: 1}), capacity=4
    )
    cases = controller.bulkhead(2, route="cases")
    caller = Caller(key="usr_1", plan=ClientPlan.PUBLIC)

    with recording_telemetry() as recorded:
        async with controller.admit(caller):
            pass
        with pytest.raises(CallerRateLimitedError):
            async with cases.admit(caller):
                pass

    decisions = {
        (a["headache.route"], a[genai.HEADACHE_PLAN], a[genai.HEADACHE_ADMISSION]): v
        for a, v in recorded.points(metrics.ADMISSION_DECISIONS)
    }
    assert decisions == {("ask", "public", "admitted"): 1, ("cases", "public", "rate_limited"): 1}


async def test_cache_lookups_by_reason() -> None:
    cache = SemanticCache(InMemorySemanticCache(), HashingEmbedding(64), threshold=0.9)
    scope = CacheScope(("public",), "es", "v1", "extractive")

    with recording_telemetry() as recorded:
        await cache.lookup("¿Dónde está mi pedido ord_1?", scope)
        await cache.lookup("¿A qué hora abre el recinto?", scope)

    assert recorded.total(metrics.CACHE_LOOKUPS, **{genai.HEADACHE_CACHE_REASON: "personal"}) == 1
    assert recorded.total(metrics.CACHE_LOOKUPS, **{genai.HEADACHE_CACHE_REASON: "miss"}) == 1


async def test_queue_metrics_and_outbox_backlog() -> None:
    bus = TracedMessageBus(InMemoryMessageBus(max_deliveries=2), system="rabbitmq")
    outbox = InMemoryOutbox()

    async def flaky(message: Message) -> None:
        if message.delivery_count == 1:
            raise ConnectionError("caído")

    with recording_telemetry() as recorded:
        await bus.subscribe("q", flaky)
        await outbox.append([OutboxEvent("q", {}), OutboxEvent("otro", {})])
        relay = OutboxRelay(outbox, bus, topic_prefix="q")
        assert await relay.report_backlog() == 1
        await relay.drain()
        assert await relay.report_backlog() == 0

    destination = {"messaging.destination.name": "q"}
    assert recorded.total(metrics.MESSAGES_SENT, **destination) == 1
    assert recorded.total(metrics.MESSAGE_PROCESS_DURATION, **destination) == 2
    assert (
        recorded.total(
            metrics.MESSAGE_PROCESS_DURATION, **destination, **{genai.ERROR_TYPE: "ConnectionError"}
        )
        == 1
    )
    [(attributes, backlog)] = recorded.points(metrics.OUTBOX_BACKLOG)
    assert (attributes["headache.outbox.topic_prefix"], backlog) == ("q", 0)


def test_latency_histograms_use_second_buckets(recorded_histogram: RecordedTelemetry) -> None:
    [(_, point)] = recorded_histogram.points(metrics.LLM_DURATION)
    assert list(point.explicit_bounds) == list(metrics.LATENCY_BUCKETS)


@pytest.fixture
def recorded_histogram() -> Iterator[RecordedTelemetry]:
    with recording_telemetry() as recorded:
        metrics.llm_call(seconds=0.8, attributes={genai.HEADACHE_TIER: "cheap"})
        yield recorded


# --- SLO -----------------------------------------------------------------------------------


def test_error_budget_and_burn_rate() -> None:
    slo = Slo("x", "x", objective=0.995, kind=SliKind.RATIO, window_days=30)

    assert slo.error_budget == pytest.approx(0.005)
    assert slo.burn_rate(0.005) == pytest.approx(1)
    assert slo.burn_rate(0.072) == pytest.approx(14.4)
    assert slo.budget_left(0.0025) == pytest.approx(0.5)
    assert slo.budget_left(0.01) < 0


def test_the_fast_page_burns_two_percent_of_a_30_day_budget_in_an_hour() -> None:
    slo = Slo("x", "x", objective=0.999, kind=SliKind.RATIO, window_days=30)
    page, *_ = BURN_RATE_ALERTS

    assert page.severity is Severity.PAGE
    assert page.threshold(slo) == pytest.approx(0.0144)
    assert page.budget_spent(slo, long_window_hours=1) == pytest.approx(0.02)


def test_slo_catalog_is_consistent() -> None:
    names = [slo.name for slo in SLOS]
    assert len(names) == len(set(names))
    assert {slo.kind for slo in SLOS} == {SliKind.RATIO, SliKind.LATENCY}
    with pytest.raises(ValueError, match="umbral"):
        Slo("x", "x", objective=0.9, kind=SliKind.LATENCY)
    with pytest.raises(ValueError, match="proporción"):
        Slo("x", "x", objective=1.0, kind=SliKind.RATIO)


def test_cost_budget_projects_the_last_hour() -> None:
    assert COST_BUDGET.hourly_limit() == pytest.approx(20 * 0.8 / 24)
    assert COST_BUDGET.projected_daily(1.0) == 24

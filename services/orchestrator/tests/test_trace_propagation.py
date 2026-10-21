"""Fase 11.3: un caso asíncrono es UNA traza, de la entrada al resultado.

Alta (span de la petición) → outbox → relay → cola → triaje → outbox → relay → cola →
especialista (agente, tools, modelo) → outbox → relay → cola de resultados.

Cada salto ocurre en un contexto vacío (`contextvars.Context()`), como en otro proceso: la
traza solo puede continuar por lo que viaja con el evento (columna del outbox y cabeceras del
mensaje), no por el contexto heredado de asyncio.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any

from opentelemetry import trace

from headache_core.adapters.memory import (
    InMemoryCaseIntakeStore,
    InMemoryCaseStore,
    InMemoryMessageBus,
    InMemoryOutbox,
)
from headache_core.adapters.otel import recording_telemetry
from headache_core.application.case_intake import CaseIntakeService, CaseRequest
from headache_core.application.llm import ObservedLLM, PriceTable
from headache_core.application.orchestration import TriageAgent
from headache_core.application.outbox import OutboxRelay
from headache_core.application.telemetry import TelemetryLLMTracer, genai
from headache_core.application.telemetry.messaging import TracedMessageBus
from headache_core.domain import CaseStatus, Specialist
from headache_core.ports import Message, MessageHandler, SpanKind, ToolCall
from orchestrator.async_flow import subscribe_workers
from orchestrator.specialists import build_specialists
from orchestrator.testing import CUSTOMER, ORDER, local_world

PENDING = "Tu solicitud queda pendiente de revisión por un agente de Headache."


def _triage() -> str:
    return json.dumps(
        {
            "intent": "refund_request",
            "language": "es",
            "order_id": ORDER,
            "urgency": "normal",
            "summary": "Pide el reembolso.",
        }
    )


def _refund_call() -> list[ToolCall]:
    arguments = json.dumps({"order_id": ORDER, "reason": "No puede asistir."})
    return [ToolCall("c1", "request_refund", arguments)]


async def _isolated(work: Callable[[], Awaitable[Any]]) -> Any:
    """Ejecuta `work` sin el contexto de quien llama: como si fuera otro proceso."""
    return await asyncio.create_task(work(), context=contextvars.Context())  # type: ignore[arg-type]


class DeferredBus(InMemoryMessageBus):
    """Guarda lo publicado y lo entrega después, cada mensaje en un contexto vacío."""

    def __init__(self) -> None:
        super().__init__()
        self.pending: list[Message] = []
        self._routes: dict[str, list[MessageHandler]] = {}

    async def publish(
        self,
        topic: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Message:
        message = Message(topic=topic, payload=payload, headers=dict(headers or {}))
        self.published.append(message)
        self.pending.append(message)
        return message

    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        self._routes.setdefault(topic, []).append(handler)

    async def pump(self) -> int:
        delivered, self.pending = self.pending, []
        for message in delivered:
            for handler in self._routes.get(message.topic, []):
                await _isolated(functools.partial(handler, message))
        return len(delivered)


async def test_an_async_case_is_one_trace_from_intake_to_outcome() -> None:
    world = local_world([_triage(), _refund_call(), PENDING])
    outbox = InMemoryOutbox()
    raw = DeferredBus()
    bus = TracedMessageBus(raw, system="rabbitmq")
    cases = InMemoryCaseStore(outbox)

    with recording_telemetry() as recorded:
        llm = ObservedLLM(world.llm, tracer=TelemetryLLMTracer(), prices=PriceTable(), name="x")
        deps = replace(world.deps, llm=llm)
        await subscribe_workers(
            bus, cases, triage=TriageAgent(llm), specialists=build_specialists(deps)
        )
        intake = CaseIntakeService(InMemoryCaseIntakeStore(outbox))
        with recorded.telemetry.span("POST /cases", kind=SpanKind.SERVER):
            submitted = await intake.submit(
                customer_id=CUSTOMER,
                idempotency_key="idem-0001-abcd",
                request=CaseRequest("Reembolso", "No puedo ir, devolvedme el dinero."),
            )
        relay = OutboxRelay(outbox, bus)
        for _ in range(5):
            published = await _isolated(relay.drain)
            if not published and not await raw.pump():
                break
            await raw.pump()

    case = await cases.get(submitted.case_id)
    assert case is not None and case.status is CaseStatus.PENDING_APPROVAL
    spans = recorded.exporter.get_finished_spans()
    tree = recorded.tree()
    assert len({s.context.trace_id for s in spans}) == 1, tree
    names = [s.name for s in spans]
    for expected in (
        "POST /cases",
        "send support.emails",
        "process support.emails",
        f"send case.triaged.{Specialist.REFUNDS.value}",
        f"process case.triaged.{Specialist.REFUNDS.value}",
        "invoke_agent headache-refunds-specialist",
        "execute_tool request_refund",
        "send case.outcomes",
    ):
        assert expected in names, tree
    assert names.count("outbox.relay") == 3, tree
    # Pendiente de aprobación humana no es un fallo de la tool.
    tool = next(s for s in spans if s.name == "execute_tool request_refund")
    assert tool.status.status_code is not trace.StatusCode.ERROR
    assert dict(tool.attributes or {})[genai.HEADACHE_TOOL_OUTCOME] == "approval_required"
    process = next(s for s in spans if s.name == "process support.emails")
    assert process.kind is trace.SpanKind.CONSUMER
    assert dict(process.attributes or {})["headache.correlation_id"] == submitted.case_id
    root = next(s for s in spans if s.name == "POST /cases")
    assert root.parent is None


async def test_a_redelivery_is_another_process_span_under_the_same_send() -> None:
    raw = InMemoryMessageBus(max_deliveries=2)
    bus = TracedMessageBus(raw, system="rabbitmq")
    attempts: list[int] = []

    async def flaky(message: Message) -> None:
        attempts.append(message.delivery_count)
        if message.delivery_count == 1:
            raise ConnectionError("modelo caído")

    with recording_telemetry() as recorded:
        await bus.subscribe("q", flaky)
        await bus.publish("q", {"correlation_id": "case_1"})

    assert attempts == [1, 2]
    spans = recorded.exporter.get_finished_spans()
    send = next(s for s in spans if s.name == "send q")
    processes = [s for s in spans if s.name == "process q"]
    assert [dict(s.attributes or {})["headache.messaging.delivery_count"] for s in processes] == [
        1,
        2,
    ]
    assert all(s.parent and s.parent.span_id == send.context.span_id for s in processes)
    assert processes[0].status.status_code is trace.StatusCode.ERROR


async def test_messages_without_context_start_a_new_trace() -> None:
    raw = InMemoryMessageBus()
    bus = TracedMessageBus(raw, system="rabbitmq")
    seen: list[Message] = []

    async def handler(message: Message) -> None:
        seen.append(message)

    with recording_telemetry() as recorded:
        await bus.subscribe("q", handler)
        # Un productor de antes de la fase 11 (o uno externo): sin cabeceras.
        await _isolated(lambda: raw.publish("q", {"n": 1}))

    (process,) = recorded.exporter.get_finished_spans()
    assert process.parent is None
    assert seen[0].headers == {}
    assert genai.ERROR_TYPE not in dict(process.attributes or {})

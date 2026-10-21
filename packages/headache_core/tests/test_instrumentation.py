"""Fase 11.2: spans de tools, agentes, retrieval, caché y admisión con la convención GenAI."""

from __future__ import annotations

import json

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import StatusCode
from pydantic import BaseModel, ConfigDict

from headache_core.adapters.local_retrieval import LocalRetrieval, TokenCoverageReranker
from headache_core.adapters.memory import (
    FakeLLM,
    HashingEmbedding,
    InMemoryRateLimiter,
    InMemorySemanticCache,
)
from headache_core.adapters.otel import recording_telemetry
from headache_core.application.admission import (
    AdmissionController,
    CallerRateLimitedError,
    plan_rules,
)
from headache_core.application.agents.runtime import AgentTurn
from headache_core.application.agents.tracing import TracedAgent
from headache_core.application.llm import ObservedLLM, PriceTable
from headache_core.application.retrieval import RetrievalRequest, RetrievalService
from headache_core.application.semantic_cache import SemanticCache
from headache_core.application.telemetry import TelemetryLLMTracer, genai
from headache_core.application.tools import (
    Tool,
    ToolContext,
    ToolExecution,
    ToolExecutor,
    ToolLoop,
    ToolRegistry,
)
from headache_core.domain.admission import Caller, ClientPlan
from headache_core.domain.semantic_cache import CacheScope
from headache_core.ports import ChatMessage, Role, ToolCall
from headache_core.ports.vector_store import VectorRecord

CONTEXT = ToolContext(customer_id="cus_1")


class LookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str


async def _lookup(args: LookupArgs, context: ToolContext) -> dict[str, str]:
    return {"order": "ord_1", "email": args.email}


REGISTRY = ToolRegistry([Tool("find_order", "Busca un pedido.", LookupArgs, _lookup)])


def _attributes(span: ReadableSpan) -> dict[str, object]:
    return dict(span.attributes or {})


class OuterExecutor(ToolExecutor):
    """Como `JournaledExecutor`: delega en otro ejecutor (el del harness)."""

    def __init__(self, inner: ToolExecutor) -> None:
        super().__init__(REGISTRY)
        self._inner = inner

    async def _execute(self, call: ToolCall, context: ToolContext) -> ToolExecution:
        return await self._inner.execute(call, context)


async def test_chained_executors_open_one_tool_span_with_redacted_content() -> None:
    call = ToolCall("c1", "find_order", json.dumps({"email": "ana@example.com"}))

    with recording_telemetry(capture_content=True) as recorded:
        execution = await OuterExecutor(ToolExecutor(REGISTRY)).execute(call, CONTEXT)

    assert execution.error is None
    (span,) = recorded.exporter.get_finished_spans()
    attributes = _attributes(span)
    assert span.name == "execute_tool find_order"
    assert attributes[genai.OPERATION_NAME] == "execute_tool"
    assert attributes[genai.TOOL_CALL_ID] == "c1"
    assert attributes[genai.LANGFUSE_OBSERVATION_TYPE] == "tool"
    assert attributes[genai.HEADACHE_TOOL_READ_ONLY] is True
    assert "<EMAIL_ADDRESS>" in str(attributes[genai.TOOL_CALL_ARGUMENTS])
    assert "ana@example.com" not in str(attributes)


async def test_a_tool_error_returned_to_the_model_marks_the_span() -> None:
    call = ToolCall("c1", "find_order", "{}")

    with recording_telemetry() as recorded:
        execution = await ToolExecutor(REGISTRY).execute(call, CONTEXT)

    (span,) = recorded.exporter.get_finished_spans()
    assert execution.error == "invalid_arguments"
    assert span.status.status_code is StatusCode.ERROR
    assert _attributes(span)[genai.ERROR_TYPE] == "invalid_arguments"
    # Sin captura de contenido, ni argumentos ni resultado.
    assert genai.TOOL_CALL_ARGUMENTS not in _attributes(span)


class LoopAgent:
    """Un sabor mínimo: el bucle manual de tools con el gateway observado."""

    flavor = "native"

    def __init__(self, llm: FakeLLM) -> None:
        observed = ObservedLLM(llm, tracer=TelemetryLLMTracer(), prices=PriceTable(), name="x")
        self._loop = ToolLoop(observed, REGISTRY)

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        result = await self._loop.run([ChatMessage(Role.USER, message)], context)
        return AgentTurn(result.answer, result.stop_reason, guardrails=("pii_redacted",))


async def test_agent_turn_is_the_parent_of_model_and_tool_spans() -> None:
    llm = FakeLLM([[ToolCall("c1", "find_order", '{"email": "a@b.es"}')], "Tu pedido es ord_1."])
    agent = TracedAgent(LoopAgent(llm), name="booking")

    with recording_telemetry() as recorded:
        await agent.respond("thread_9", "¿Mi pedido?", CONTEXT)

    spans = recorded.exporter.get_finished_spans()
    root = next(s for s in spans if s.name == "invoke_agent booking")
    children = sorted(
        s.name for s in spans if s.parent and s.parent.span_id == root.context.span_id
    )
    assert children == ["chat fake-llm", "chat fake-llm", "execute_tool find_order"]
    attributes = _attributes(root)
    assert attributes[genai.CONVERSATION_ID] == "thread_9"
    assert attributes[genai.LANGFUSE_SESSION_ID] == "thread_9"
    assert attributes[genai.HEADACHE_STOP_REASON] == "answer"
    assert attributes[genai.HEADACHE_GUARDRAILS] == ("pii_redacted",)
    assert all(s.context.trace_id == root.context.trace_id for s in spans)


async def test_retrieval_span_with_search_and_rerank_children() -> None:
    embeddings = HashingEmbedding(64)
    texts = ["Refund within 48 hours.", "Madrid venue accessible."]
    vectors = await embeddings.embed_texts(texts)
    records = [
        VectorRecord(f"doc::{i}", text, vector, {"document_id": f"doc{i}", "acl": "public"})
        for i, (text, vector) in enumerate(zip(texts, vectors, strict=True))
    ]
    service = RetrievalService(
        LocalRetrieval(records, embeddings=embeddings), reranker=TokenCoverageReranker()
    )

    with recording_telemetry() as recorded:
        await service.retrieve(RetrievalRequest("Refund", strategy="rerank", top_k=1))

    spans = recorded.spans()
    root = spans["retrieval knowledge"]
    attributes = _attributes(root)
    assert attributes[genai.OPERATION_NAME] == "retrieval"
    assert attributes[genai.DATA_SOURCE_ID] == "knowledge"
    assert attributes[genai.LANGFUSE_OBSERVATION_TYPE] == "retriever"
    assert attributes[genai.HEADACHE_RETRIEVAL_RESULTS] == 1
    for child in ("search", "rerank"):
        parent = spans[child].parent
        assert parent is not None
        assert parent.span_id == root.context.span_id


@pytest.mark.parametrize(
    ("question", "reason"),
    [("¿Dónde está mi pedido ord_1?", "personal"), ("¿A qué hora abre el recinto?", "miss")],
)
async def test_cache_lookup_span_says_why(question: str, reason: str) -> None:
    cache = SemanticCache(InMemorySemanticCache(), HashingEmbedding(64), threshold=0.9)

    with recording_telemetry() as recorded:
        await cache.lookup(question, CacheScope(("public",), "es", "v1", "extractive"))

    (span,) = recorded.exporter.get_finished_spans()
    assert span.name == "semantic_cache.lookup"
    assert _attributes(span)[genai.HEADACHE_CACHE_REASON] == reason


async def test_admission_annotates_the_request_span() -> None:
    controller = AdmissionController(
        InMemoryRateLimiter(), rules=plan_rules({ClientPlan.PUBLIC: 1}), capacity=4
    )
    caller = Caller(key="usr_1", plan=ClientPlan.PUBLIC)

    with recording_telemetry() as recorded:
        with recorded.telemetry.span("POST /ask"):
            async with controller.admit(caller):
                pass
        with pytest.raises(CallerRateLimitedError), recorded.telemetry.span("POST /ask"):
            async with controller.admit(caller):
                pass

    requests = [s for s in recorded.exporter.get_finished_spans() if s.name == "POST /ask"]
    outcomes = [_attributes(s)[genai.HEADACHE_ADMISSION] for s in requests]
    assert outcomes == ["admitted", "rate_limited"]
    assert all(_attributes(s)[genai.HEADACHE_PLAN] == "public" for s in requests)
    limits = [s for s in recorded.exporter.get_finished_spans() if s.name == "rate_limit"]
    assert len(limits) == 2

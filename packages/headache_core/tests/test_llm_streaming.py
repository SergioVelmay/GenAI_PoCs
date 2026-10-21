"""Streaming en la capa de modelos (fase 10.4): adaptadores y capas del gateway."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any, cast

import httpx
import pytest
from botocore.exceptions import EventStreamError
from openai import AsyncAzureOpenAI

from headache_core.adapters.azure_openai import AzureOpenAILLM
from headache_core.adapters.bedrock import BedrockConverseLLM
from headache_core.adapters.memory import FakeLLM, RecordingTracer
from headache_core.application.llm import (
    CircuitBreakerLLM,
    CircuitState,
    ConcurrencyLimitedLLM,
    FallbackLLM,
    ObservedLLM,
    PriceTable,
    RateLimitedLLM,
)
from headache_core.application.llm.streaming import collect, stream_from, with_deadline
from headache_core.ports import (
    ChatMessage,
    LLMContentFilterError,
    LLMDeadlineExceededError,
    LLMOverloadedError,
    LLMRateLimitError,
    LLMResponse,
    LLMStreamEvent,
    LLMTransientError,
    Role,
    TextDelta,
    TokenUsage,
)

HELLO = [ChatMessage(Role.USER, "hola")]


class BrokenStreamLLM:
    """Emite `before` trozos y después falla con `error` (o termina bien si es None)."""

    def __init__(self, before: int, error: Exception | None, *, delay: float = 0.0) -> None:
        self.before = before
        self.error = error
        self.delay = delay
        self.calls = 0

    async def complete(self, messages: Sequence[ChatMessage], **_: object) -> LLMResponse:
        raise AssertionError("Solo streaming")

    async def stream(
        self, messages: Sequence[ChatMessage], **_: object
    ) -> AsyncIterator[LLMStreamEvent]:
        self.calls += 1
        for i in range(self.before):
            await asyncio.sleep(self.delay)
            yield TextDelta(f"t{i} ")
        if self.error is not None:
            raise self.error
        yield LLMResponse("ok", "fake", TokenUsage(1, self.before), "fake")


class CompleteOnlyLLM:
    async def complete(self, messages: Sequence[ChatMessage], **_: object) -> LLMResponse:
        return LLMResponse("todo de golpe", "fake", TokenUsage(1, 3), "fake")


async def _texts(events: AsyncIterator[LLMStreamEvent]) -> list[str]:
    return [event.text async for event in events if isinstance(event, TextDelta)]


# --- Utilidades --------------------------------------------------------------------------


async def test_fake_llm_streams_word_by_word_and_ends_with_the_full_response() -> None:
    events = [e async for e in FakeLLM(["Abren 15 minutos antes."]).stream(HELLO)]

    texts = [e.text for e in events if isinstance(e, TextDelta)]
    assert texts == ["Abren ", "15 ", "minutos ", "antes."]
    assert isinstance(events[-1], LLMResponse) and events[-1].content == "Abren 15 minutos antes."


async def test_a_model_without_streaming_is_adapted_to_one_chunk() -> None:
    llm = CompleteOnlyLLM()

    texts = await _texts(stream_from(llm, HELLO))

    assert texts == ["todo de golpe"]


async def test_deadline_cuts_a_stream_that_stalls_between_chunks() -> None:
    slow = BrokenStreamLLM(5, None, delay=0.05)

    with pytest.raises(LLMDeadlineExceededError):
        await collect(with_deadline(slow.stream(HELLO), 0.12))


# --- Fallback ----------------------------------------------------------------------------


async def test_failure_before_the_first_chunk_falls_back() -> None:
    llm = FallbackLLM(
        [BrokenStreamLLM(0, LLMTransientError("503")), FakeLLM(["desde el respaldo"])],
        max_retries=0,
    )

    assert await _texts(llm.stream(HELLO)) == ["desde ", "el ", "respaldo"]


async def test_failure_after_the_first_chunk_is_not_spliced_with_another_answer() -> None:
    secondary = FakeLLM(["otra respuesta"])
    llm = FallbackLLM(
        [BrokenStreamLLM(2, LLMTransientError("conexión cortada")), secondary], max_retries=2
    )
    seen: list[str] = []

    with pytest.raises(LLMTransientError, match="cortada"):
        async for event in llm.stream(HELLO):
            if isinstance(event, TextDelta):
                seen.append(event.text)

    assert seen == ["t0 ", "t1 "]
    assert secondary.calls == []


async def test_rate_limited_stream_jumps_to_next_provider_without_retrying() -> None:
    primary = BrokenStreamLLM(0, LLMRateLimitError("429"))
    llm = FallbackLLM([primary, FakeLLM(["ok"])], max_retries=3)

    assert await _texts(llm.stream(HELLO)) == ["ok"]
    assert primary.calls == 1


# --- Capas -------------------------------------------------------------------------------


async def test_observed_stream_records_cost_once_at_the_end() -> None:
    tracer = RecordingTracer()
    llm = ObservedLLM(
        FakeLLM(["una respuesta corta"], model="gpt-4.1-mini"),
        tracer=tracer,
        prices=PriceTable(),
        name="azure:cheap",
    )

    await collect(llm.stream(HELLO))

    [record] = tracer.records
    assert record.response is not None and record.response.content == "una respuesta corta"
    assert record.cost_usd is not None and record.cost_usd > 0


async def test_observed_stream_cut_by_the_client_is_recorded_as_failed() -> None:
    tracer = RecordingTracer()
    llm = ObservedLLM(BrokenStreamLLM(5, None), tracer=tracer, prices=PriceTable(), name="x")

    events = llm.stream(HELLO)
    await anext(events)
    await events.aclose()  # El usuario cierra la pestaña.

    assert tracer.records[0].response is None
    assert tracer.records[0].error is not None


async def test_rate_limited_stream_spends_quota_like_a_normal_call() -> None:
    llm = RateLimitedLLM(FakeLLM(), requests_per_minute=1, tokens_per_minute=1_000_000, max_wait=0)
    await collect(llm.stream(HELLO))

    with pytest.raises(LLMOverloadedError):
        await collect(llm.stream(HELLO))


async def test_bulkhead_holds_the_slot_while_streaming_and_frees_it_on_close() -> None:
    llm = ConcurrencyLimitedLLM(BrokenStreamLLM(3, None), max_concurrency=1, max_wait=0)

    events = llm.stream(HELLO)
    await anext(events)
    assert llm.in_flight == 1
    with pytest.raises(LLMOverloadedError):
        await collect(llm.stream(HELLO))
    await events.aclose()

    assert llm.in_flight == 0
    await collect(llm.stream(HELLO))


async def test_mid_stream_failures_open_the_circuit() -> None:
    breaker = CircuitBreakerLLM(
        BrokenStreamLLM(1, LLMTransientError("corte")), name="aws:cheap", failure_threshold=2
    )
    for _ in range(2):
        with pytest.raises(LLMTransientError):
            await collect(breaker.stream(HELLO))

    with pytest.raises(LLMOverloadedError):
        await collect(breaker.stream(HELLO))
    assert breaker.state is CircuitState.OPEN


async def test_client_closing_the_stream_does_not_count_against_the_provider() -> None:
    breaker = CircuitBreakerLLM(BrokenStreamLLM(5, None), name="x", failure_threshold=1)

    events = breaker.stream(HELLO)
    await anext(events)
    await events.aclose()

    assert breaker.state is CircuitState.CLOSED


# --- Azure OpenAI ------------------------------------------------------------------------


def _sse(*chunks: dict[str, Any]) -> bytes:
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return body.encode()


def _chunk(content: str | None = None, finish: str | None = None) -> dict[str, Any]:
    choice = {"index": 0, "delta": {"content": content} if content else {}, "finish_reason": finish}
    return {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-4.1-mini-2025-04-14",
        "choices": [choice],
    }


def _azure(handler: Any) -> AzureOpenAILLM:
    client = AsyncAzureOpenAI(
        azure_endpoint="https://headache-test.openai.azure.com",
        api_key="test",
        api_version="2024-10-21",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return AzureOpenAILLM(client, "gpt-4.1-mini")


async def test_azure_stream_yields_deltas_and_reads_usage_from_the_last_chunk() -> None:
    seen: dict[str, Any] = {}
    usage_chunk = {
        **_chunk(),
        "choices": [],
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 3,
            "total_tokens": 1203,
            "prompt_tokens_details": {"cached_tokens": 1024},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        stream = _sse(_chunk("Abren "), _chunk("a las 20:00"), _chunk(finish="stop"), usage_chunk)
        return httpx.Response(200, content=stream, headers={"content-type": "text/event-stream"})

    events = [e async for e in _azure(handler).stream(HELLO)]

    assert seen["body"]["stream"] is True
    assert seen["body"]["stream_options"] == {"include_usage": True}
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Abren ", "a las 20:00"]
    final = events[-1]
    assert isinstance(final, LLMResponse)
    assert final.content == "Abren a las 20:00"
    assert final.usage == TokenUsage(1200, 3, cached_input_tokens=1024)
    assert final.finish_reason == "stop"


async def test_azure_stream_cut_by_the_content_filter_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        stream = _sse(_chunk("Empiezo "), _chunk(finish="content_filter"))
        return httpx.Response(200, content=stream, headers={"content-type": "text/event-stream"})

    with pytest.raises(LLMContentFilterError):
        await collect(_azure(handler).stream(HELLO))


async def test_azure_stream_rate_limit_before_starting() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "x"}})

    with pytest.raises(LLMRateLimitError) as info:
        await collect(_azure(handler).stream(HELLO))
    assert info.value.retry_after == 3.0


# --- Bedrock ConverseStream --------------------------------------------------------------


class FakeBedrockClient:
    def __init__(self, events: list[dict[str, Any]], error_after: int | None = None) -> None:
        self.events = events
        self.error_after = error_after
        self.requests: list[dict[str, Any]] = []

    def converse_stream(self, **request: Any) -> dict[str, Iterator[dict[str, Any]]]:
        self.requests.append(request)
        return {"stream": self._iterate()}

    def _iterate(self) -> Iterator[dict[str, Any]]:
        for i, event in enumerate(self.events):
            if self.error_after is not None and i == self.error_after:
                raise EventStreamError(
                    {"Error": {"Code": "throttlingException", "Message": "Too many tokens"}},
                    "ConverseStream",
                )
            yield event


def _bedrock(client: FakeBedrockClient) -> BedrockConverseLLM:
    return BedrockConverseLLM(cast(Any, client), "eu.amazon.nova-lite-v1:0")


BEDROCK_EVENTS: list[dict[str, Any]] = [
    {"messageStart": {"role": "assistant"}},
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Abren "}}},
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "a las 20:00"}}},
    {"contentBlockStop": {"contentBlockIndex": 0}},
    {"messageStop": {"stopReason": "end_turn"}},
    {
        "metadata": {
            "usage": {"inputTokens": 10, "outputTokens": 4, "cacheReadInputTokens": 1000},
            "metrics": {"latencyMs": 120},
        }
    },
]


async def test_bedrock_stream_yields_deltas_and_usage() -> None:
    client = FakeBedrockClient(BEDROCK_EVENTS)

    events = [e async for e in _bedrock(client).stream(HELLO, max_tokens=50)]

    assert client.requests[0]["inferenceConfig"] == {"temperature": 0.0, "maxTokens": 50}
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Abren ", "a las 20:00"]
    final = events[-1]
    assert isinstance(final, LLMResponse)
    assert final.usage == TokenUsage(1010, 4, cached_input_tokens=1000)
    assert (final.finish_reason, final.provider) == ("end_turn", "aws")


async def test_bedrock_error_event_mid_stream_is_translated() -> None:
    client = FakeBedrockClient(BEDROCK_EVENTS, error_after=2)
    seen: list[str] = []

    with pytest.raises(LLMRateLimitError):
        async for event in _bedrock(client).stream(HELLO):
            if isinstance(event, TextDelta):
                seen.append(event.text)

    assert seen == ["Abren "]

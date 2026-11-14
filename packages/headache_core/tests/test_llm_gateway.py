from __future__ import annotations

from decimal import Decimal

import pytest

from headache_core.adapters.memory import FakeLLM, RecordingTracer
from headache_core.application.llm import (
    CompositeTracer,
    FallbackLLM,
    ObservedLLM,
    PriceTable,
    RateLimitedLLM,
    TokenBucket,
)
from headache_core.ports import (
    ChatMessage,
    LLMError,
    LLMRateLimitError,
    LLMTransientError,
    Role,
)

HELLO = [ChatMessage(Role.USER, "hola")]


class FakeTime:
    """Reloj y `sleep` falsos: el test controla el paso del tiempo."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_fallback_retries_transient_errors_on_same_provider() -> None:
    time = FakeTime()
    primary = FakeLLM([LLMTransientError("503"), "ok"])
    llm = FallbackLLM([primary, FakeLLM(["respaldo"])], max_retries=1, sleep=time.sleep)

    response = await llm.complete(HELLO)

    assert response.content == "ok"
    assert len(time.sleeps) == 1


async def test_rate_limit_jumps_straight_to_next_provider() -> None:
    time = FakeTime()
    primary, secondary = FakeLLM([LLMRateLimitError("429")]), FakeLLM(["desde aws"])
    llm = FallbackLLM([primary, secondary], max_retries=3, sleep=time.sleep)

    response = await llm.complete(HELLO)

    assert response.content == "desde aws"
    assert len(primary.calls) == 1
    assert time.sleeps == []


async def test_fallback_walks_a_chain_of_three_clouds() -> None:
    """2.G2: Azure sin cuota (salta sin reintentar), Bedrock caído (reintenta y salta), Gemini."""
    time = FakeTime()
    azure = FakeLLM([LLMRateLimitError("429")])
    aws = FakeLLM([LLMTransientError("503"), LLMTransientError("503")])
    gcp = FakeLLM(["desde gemini"])
    llm = FallbackLLM([azure, aws, gcp], max_retries=1, sleep=time.sleep)

    response = await llm.complete(HELLO)

    assert response.content == "desde gemini"
    assert (len(azure.calls), len(aws.calls), len(gcp.calls)) == (1, 2, 1)
    assert len(time.sleeps) == 1  # Un solo backoff: el reintento de Bedrock.


async def test_non_transient_errors_are_not_retried() -> None:
    secondary = FakeLLM(["no debería llamarse"])
    llm = FallbackLLM([FakeLLM([LLMError("400")]), secondary])

    with pytest.raises(LLMError, match="400"):
        await llm.complete(HELLO)
    assert secondary.calls == []


async def test_all_providers_failing_raises_transient_error() -> None:
    time = FakeTime()
    llm = FallbackLLM(
        [FakeLLM([LLMRateLimitError("429")]), FakeLLM([LLMTransientError("503")])],
        max_retries=0,
        sleep=time.sleep,
    )

    with pytest.raises(LLMTransientError) as info:
        await llm.complete(HELLO)
    assert isinstance(info.value.__cause__, LLMTransientError)


async def test_token_bucket_waits_for_refill() -> None:
    time = FakeTime()
    bucket = TokenBucket(2, refill_per_second=1.0, clock=time.clock, sleep=time.sleep)

    assert await bucket.acquire() == 0
    assert await bucket.acquire() == 0
    assert await bucket.acquire() == pytest.approx(1.0)


async def test_rate_limited_llm_spaces_requests() -> None:
    time = FakeTime()
    llm = RateLimitedLLM(
        FakeLLM(),
        requests_per_minute=1,
        tokens_per_minute=1_000_000,
        clock=time.clock,
        sleep=time.sleep,
    )

    await llm.complete(HELLO)
    await llm.complete(HELLO)

    assert sum(time.sleeps) == pytest.approx(60.0)


async def test_observed_llm_records_usage_and_cost() -> None:
    tracer = RecordingTracer()
    llm = ObservedLLM(
        FakeLLM(["una respuesta corta"], model="gpt-4.1-mini"),
        tracer=tracer,
        prices=PriceTable(),
        name="azure:strong",
        metadata={"tier": "strong"},
    )

    await llm.complete(HELLO)

    [record] = tracer.records
    assert record.name == "azure:strong"
    assert record.response is not None
    # 1 token de entrada x 0.40 + 3 de salida x 1.60, por millón.
    assert record.cost_usd == Decimal("0.0000052")


async def test_observed_llm_records_failures_and_reraises() -> None:
    tracer = RecordingTracer()
    llm = ObservedLLM(
        FakeLLM([LLMTransientError("503")]), tracer=tracer, prices=PriceTable(), name="x"
    )

    with pytest.raises(LLMTransientError):
        await llm.complete(HELLO)
    assert isinstance(tracer.records[0].error, LLMTransientError)


async def test_composite_tracer_fans_out() -> None:
    first, second = RecordingTracer(), RecordingTracer()
    llm = ObservedLLM(
        FakeLLM(["ok"]), tracer=CompositeTracer([first, second]), prices=PriceTable(), name="x"
    )

    await llm.complete(HELLO)

    assert first.records[0].response == second.records[0].response

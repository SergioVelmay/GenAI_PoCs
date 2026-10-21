"""Resiliencia del cliente LLM bajo carga (fase 10.1): breaker, bulkhead, backpressure,
Retry-After y presupuesto de tiempo. Reloj falso: deterministas y sin esperas reales."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence

import pytest

from headache_core.adapters.memory import FakeLLM
from headache_core.application.llm import (
    CircuitBreakerLLM,
    CircuitState,
    ConcurrencyLimitedLLM,
    FallbackLLM,
    RateLimitedLLM,
    remaining,
    time_budget,
)
from headache_core.ports import (
    ChatMessage,
    LLMDeadlineExceededError,
    LLMError,
    LLMOverloadedError,
    LLMPort,
    LLMRateLimitError,
    LLMResponse,
    LLMTransientError,
    Role,
    TokenUsage,
)

HELLO = [ChatMessage(Role.USER, "hola")]


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FailingLLM:
    """Falla siempre con el mismo error y cuenta las llamadas."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def complete(self, messages: Sequence[ChatMessage], **_: object) -> LLMResponse:
        self.calls += 1
        raise self.error


class BlockingLLM:
    """Responde cuando el test abre la puerta: simula llamadas lentas en vuelo."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.started = 0

    async def complete(self, messages: Sequence[ChatMessage], **_: object) -> LLMResponse:
        self.started += 1
        await self.gate.wait()
        return LLMResponse("ok", "fake", TokenUsage(1, 1), "fake")


def _breaker(inner: LLMPort, time: FakeTime, **kwargs: float) -> CircuitBreakerLLM:
    return CircuitBreakerLLM(
        inner,
        name="azure:strong",
        failure_threshold=int(kwargs.get("threshold", 3)),
        reset_timeout=kwargs.get("reset", 30.0),
        clock=time.clock,
    )


def _state(breaker: CircuitBreakerLLM) -> CircuitState:
    """Lee el estado sin que mypy lo estreche entre asserts (la propiedad cambia con el reloj)."""
    return breaker.state


# --- Circuit breaker ---------------------------------------------------------------------


async def test_breaker_opens_after_consecutive_failures_and_fails_fast() -> None:
    time = FakeTime()
    inner = FailingLLM(LLMTransientError("503"))
    breaker = _breaker(inner, time)

    for _ in range(3):
        with pytest.raises(LLMTransientError):
            await breaker.complete(HELLO)
    time.now = 10.0
    with pytest.raises(LLMOverloadedError) as info:
        await breaker.complete(HELLO)

    assert _state(breaker) is CircuitState.OPEN
    assert inner.calls == 3  # La cuarta no llega al proveedor.
    assert info.value.retry_after == pytest.approx(20.0)


async def test_breaker_success_resets_the_failure_count() -> None:
    time = FakeTime()
    error = LLMTransientError("503")
    breaker = _breaker(FakeLLM([error, error, "ok", error, error]), time)

    for _ in range(5):
        with contextlib.suppress(LLMTransientError):
            await breaker.complete(HELLO)

    assert _state(breaker) is CircuitState.CLOSED


async def test_half_open_lets_one_probe_through_and_closes_on_success() -> None:
    time = FakeTime()
    error = LLMTransientError("503")
    inner = FakeLLM([error, error, error, "recuperado"])
    breaker = _breaker(inner, time)
    for _ in range(3):
        with pytest.raises(LLMTransientError):
            await breaker.complete(HELLO)

    time.now = 30.0
    assert _state(breaker) is CircuitState.HALF_OPEN
    response = await breaker.complete(HELLO)

    assert response.content == "recuperado"
    assert _state(breaker) is CircuitState.CLOSED


async def test_failed_probe_reopens_and_concurrent_calls_are_rejected() -> None:
    time = FakeTime()
    breaker = _breaker(FailingLLM(LLMTransientError("503")), time, threshold=1, reset=5.0)
    with pytest.raises(LLMTransientError):
        await breaker.complete(HELLO)
    time.now = 5.0

    blocking = BlockingLLM()
    breaker._inner = blocking  # El proveedor sigue colgado durante la prueba.
    probe = asyncio.create_task(breaker.complete(HELLO))
    await asyncio.sleep(0)
    with pytest.raises(LLMOverloadedError):
        await breaker.complete(HELLO)  # Solo una llamada de prueba a la vez.
    assert blocking.started == 1
    blocking.gate.set()
    await probe

    assert _state(breaker) is CircuitState.CLOSED


async def test_probe_failure_reopens_the_circuit() -> None:
    time = FakeTime()
    breaker = _breaker(FailingLLM(LLMTransientError("503")), time, threshold=2, reset=5.0)
    for _ in range(2):
        with pytest.raises(LLMTransientError):
            await breaker.complete(HELLO)
    time.now = 5.0

    with pytest.raises(LLMTransientError):
        await breaker.complete(HELLO)  # La prueba falla: un solo fallo basta para reabrir.

    assert _state(breaker) is CircuitState.OPEN


async def test_rate_limit_with_retry_after_opens_immediately_for_that_long() -> None:
    time = FakeTime()
    breaker = _breaker(FailingLLM(LLMRateLimitError("429", retry_after=12.0)), time)

    with pytest.raises(LLMRateLimitError):
        await breaker.complete(HELLO)

    assert _state(breaker) is CircuitState.OPEN
    time.now = 12.0
    assert _state(breaker) is CircuitState.HALF_OPEN


@pytest.mark.parametrize(
    "error",
    [LLMError("400 petición inválida"), LLMOverloadedError("cuota del cliente")],
    ids=["provider-healthy", "local-rejection"],
)
async def test_errors_that_say_nothing_bad_about_the_provider_do_not_trip(
    error: Exception,
) -> None:
    breaker = _breaker(FailingLLM(error), FakeTime(), threshold=1)

    for _ in range(5):
        with pytest.raises(LLMError):
            await breaker.complete(HELLO)

    assert _state(breaker) is CircuitState.CLOSED


# --- Bulkhead ----------------------------------------------------------------------------


async def test_bulkhead_rejects_when_full_instead_of_queueing() -> None:
    blocking = BlockingLLM()
    llm = ConcurrencyLimitedLLM(blocking, max_concurrency=1, max_wait=0)

    first = asyncio.create_task(llm.complete(HELLO))
    await asyncio.sleep(0)
    with pytest.raises(LLMOverloadedError):
        await llm.complete(HELLO)
    blocking.gate.set()
    await first

    assert (llm.rejected, llm.in_flight, blocking.started) == (1, 0, 1)


async def test_bulkhead_waits_for_a_slot_within_max_wait() -> None:
    blocking = BlockingLLM()
    llm = ConcurrencyLimitedLLM(blocking, max_concurrency=1, max_wait=1.0)

    first = asyncio.create_task(llm.complete(HELLO))
    second = asyncio.create_task(llm.complete(HELLO))
    await asyncio.sleep(0)
    assert llm.waiting == 1
    blocking.gate.set()
    await asyncio.gather(first, second)

    assert (llm.rejected, blocking.started) == (0, 2)


# --- Backpressure en la cuota del cliente ------------------------------------------------


async def test_rate_limiter_rejects_with_retry_after_instead_of_waiting_forever() -> None:
    time = FakeTime()
    llm = RateLimitedLLM(
        FakeLLM(),
        requests_per_minute=1,
        tokens_per_minute=1_000_000,
        max_wait=5.0,
        clock=time.clock,
        sleep=time.sleep,
    )
    await llm.complete(HELLO)

    with pytest.raises(LLMOverloadedError) as info:
        await llm.complete(HELLO)

    assert info.value.retry_after == pytest.approx(60.0)
    assert time.sleeps == []


async def test_rejected_reservation_is_refunded() -> None:
    time = FakeTime()
    inner = FakeLLM()
    llm = RateLimitedLLM(
        inner,
        requests_per_minute=2,
        tokens_per_minute=100,
        max_wait=0,
        clock=time.clock,
        sleep=time.sleep,
    )
    await llm.complete(HELLO, max_tokens=48)  # ~50 tokens: queda 1 petición y 50 tokens.

    with pytest.raises(LLMOverloadedError):
        await llm.complete(HELLO, max_tokens=98)  # Hay petición, pero no 100 tokens.

    # Si la petición reservada no se hubiera devuelto, esta tercera llamada no cabría.
    await llm.complete(HELLO, max_tokens=48)
    assert len(inner.calls) == 2


async def test_rate_limiter_waits_are_bounded_by_the_time_budget() -> None:
    time = FakeTime()
    llm = RateLimitedLLM(
        FakeLLM(),
        requests_per_minute=60,
        tokens_per_minute=1_000_000,
        clock=time.clock,
        sleep=time.sleep,
    )
    for _ in range(60):
        await llm.complete(HELLO)

    with time_budget(0.5, clock=time.clock), pytest.raises(LLMOverloadedError):
        await llm.complete(HELLO)  # Necesita 1 s; quedan 0,5.
    with time_budget(2.0, clock=time.clock):
        await llm.complete(HELLO)
    assert time.sleeps == [pytest.approx(1.0)]


# --- Fallback: Retry-After y presupuesto -------------------------------------------------


async def test_fallback_honours_retry_after_on_transient_errors() -> None:
    time = FakeTime()
    llm = FallbackLLM(
        [FakeLLM([LLMTransientError("503", retry_after=2.0), "ok"])],
        max_retries=1,
        base_delay=0.01,
        sleep=time.sleep,
        clock=time.clock,
    )

    assert (await llm.complete(HELLO)).content == "ok"
    assert time.sleeps == [2.0]


async def test_fallback_skips_provider_when_retry_after_is_too_long() -> None:
    time = FakeTime()
    secondary = FakeLLM(["desde aws"])
    llm = FallbackLLM(
        [FakeLLM([LLMTransientError("503", retry_after=60.0)]), secondary],
        max_retries=3,
        max_retry_after=5.0,
        sleep=time.sleep,
        clock=time.clock,
    )

    assert (await llm.complete(HELLO)).content == "desde aws"
    assert time.sleeps == []


async def test_open_circuit_jumps_to_next_provider_without_calling_the_broken_one() -> None:
    time = FakeTime()
    broken = FailingLLM(LLMTransientError("503"))
    primary = _breaker(broken, time, threshold=2)
    llm = FallbackLLM(
        [primary, FakeLLM()], max_retries=1, base_delay=0, sleep=time.sleep, clock=time.clock
    )

    for _ in range(5):
        await llm.complete(HELLO)

    # 2 fallos (llamada + reintento) abren el circuito; las 4 siguientes ni lo intentan.
    assert broken.calls == 2
    assert _state(primary) is CircuitState.OPEN


async def test_all_providers_saturated_returns_the_shortest_retry_after() -> None:
    llm = FallbackLLM(
        [
            FailingLLM(LLMRateLimitError("429", retry_after=8.0)),
            FailingLLM(LLMOverloadedError("circuito abierto", retry_after=3.0)),
        ]
    )

    with pytest.raises(LLMTransientError) as info:
        await llm.complete(HELLO)

    assert info.value.retry_after == 3.0


async def test_budget_too_short_for_the_backoff_stops_retrying() -> None:
    time = FakeTime()
    failing = FailingLLM(LLMTransientError("503", retry_after=2.0))
    llm = FallbackLLM([failing], max_retries=3, sleep=time.sleep, clock=time.clock)

    with time_budget(2.0, clock=time.clock), pytest.raises(LLMTransientError):
        await llm.complete(HELLO)  # Esperar 2 s agotaría el presupuesto: no reintenta.

    assert (failing.calls, time.sleeps) == (1, [])


async def test_exhausted_budget_calls_nobody() -> None:
    time = FakeTime()
    primary, secondary = FakeLLM(), FakeLLM()
    llm = FallbackLLM([primary, secondary], sleep=time.sleep, clock=time.clock)

    with time_budget(0.0, clock=time.clock), pytest.raises(LLMDeadlineExceededError):
        await llm.complete(HELLO)

    assert (primary.calls, secondary.calls) == ([], [])


async def test_slow_provider_is_cut_by_the_budget_and_not_retried() -> None:
    slow = BlockingLLM()
    secondary = FakeLLM(["no debería llamarse"])
    llm = FallbackLLM([slow, secondary], max_retries=2)

    with time_budget(0.05), pytest.raises(LLMDeadlineExceededError):
        await llm.complete(HELLO)

    assert (slow.started, secondary.calls) == (1, [])


async def test_nested_budgets_keep_the_strictest() -> None:
    time = FakeTime()
    with time_budget(10, clock=time.clock), time_budget(3, clock=time.clock):
        assert remaining(clock=time.clock) == 3
        with time_budget(30, clock=time.clock):
            assert remaining(clock=time.clock) == 3
    assert remaining(clock=time.clock) is None

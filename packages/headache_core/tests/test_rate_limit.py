"""Rate limiting y admisión (fase 10.2): GCRA, cuota compartida del modelo y load shedding."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from headache_core.adapters.memory import FakeLLM, InMemoryRateLimiter
from headache_core.application.admission import (
    AdmissionController,
    CallerRateLimitedError,
    ServiceOverloadedError,
    plan_rules,
)
from headache_core.application.llm import SharedQuotaLLM, time_budget
from headache_core.domain.admission import Caller, ClientPlan, access_scope, has_capacity
from headache_core.ports import (
    ChatMessage,
    LLMOverloadedError,
    RateLimitDecision,
    RateLimiterUnavailableError,
    RateLimitRequest,
    RateLimitRule,
    Role,
)

HELLO = [ChatMessage(Role.USER, "hola")]
PER_MINUTE_3 = RateLimitRule("user_public", 3, 60.0)


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class DownLimiter:
    """Redis caído."""

    async def acquire(self, requests: Sequence[RateLimitRequest]) -> RateLimitDecision:
        raise RateLimiterUnavailableError("connection refused")


# --- GCRA --------------------------------------------------------------------------------


async def test_gcra_allows_the_burst_then_denies_with_retry_after() -> None:
    time = FakeTime()
    limiter = InMemoryRateLimiter(clock=time.clock)
    request = [RateLimitRequest("user:u1", PER_MINUTE_3)]

    decisions = [await limiter.acquire(request) for _ in range(4)]

    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert [d.remaining for d in decisions[:3]] == [2, 1, 0]
    assert decisions[3].retry_after == pytest.approx(20.0)  # Una unidad cada 60/3 s.
    assert decisions[3].limited_by == "user_public"


async def test_gcra_refills_continuously_not_in_fixed_windows() -> None:
    time = FakeTime()
    limiter = InMemoryRateLimiter(clock=time.clock)
    request = [RateLimitRequest("user:u1", PER_MINUTE_3)]
    for _ in range(3):
        await limiter.acquire(request)

    time.now += 20.0  # Una unidad recuperada, no la ventana entera.
    assert (await limiter.acquire(request)).allowed
    assert not (await limiter.acquire(request)).allowed


async def test_keys_are_independent() -> None:
    limiter = InMemoryRateLimiter(clock=FakeTime().clock)
    for _ in range(3):
        await limiter.acquire([RateLimitRequest("user:u1", PER_MINUTE_3)])

    assert (await limiter.acquire([RateLimitRequest("user:u2", PER_MINUTE_3)])).allowed


async def test_multi_key_acquire_is_all_or_nothing() -> None:
    time = FakeTime()
    limiter = InMemoryRateLimiter(clock=time.clock)
    rpm = RateLimitRule("model_rpm", 10, 60.0)
    tpm = RateLimitRule("model_tpm", 1000, 60.0)

    first = await limiter.acquire(
        [RateLimitRequest("m:rpm", rpm), RateLimitRequest("m:tpm", tpm, 900)]
    )
    second = await limiter.acquire(
        [RateLimitRequest("m:rpm", rpm), RateLimitRequest("m:tpm", tpm, 900)]
    )

    assert first.allowed
    assert not second.allowed
    assert second.limited_by == "model_tpm"
    # La segunda no consumió RPM aunque había: 10 - 1 (primera) - 1 (esta) = 8.
    assert (await limiter.acquire([RateLimitRequest("m:rpm", rpm)])).remaining == 8


async def test_denied_request_consumes_nothing() -> None:
    time = FakeTime()
    limiter = InMemoryRateLimiter(clock=time.clock)
    rule = RateLimitRule("model_tpm", 100, 60.0)
    await limiter.acquire([RateLimitRequest("m", rule, 80)])

    assert not (await limiter.acquire([RateLimitRequest("m", rule, 50)])).allowed
    assert (await limiter.acquire([RateLimitRequest("m", rule, 20)])).allowed


# --- Cuota compartida del modelo ---------------------------------------------------------


def _shared(limiter: InMemoryRateLimiter, time: FakeTime, **kwargs: float) -> SharedQuotaLLM:
    return SharedQuotaLLM(
        FakeLLM(),
        limiter,
        key="azure:strong",
        requests_per_minute=int(kwargs.get("rpm", 2)),
        tokens_per_minute=int(kwargs.get("tpm", 100_000)),
        max_wait=kwargs.get("max_wait"),
        clock=time.clock,
        sleep=time.sleep,
    )


async def test_two_replicas_share_one_quota() -> None:
    time = FakeTime()
    redis = InMemoryRateLimiter(clock=time.clock)  # El mismo almacén para las dos réplicas.
    replica_a, replica_b = _shared(redis, time, max_wait=0), _shared(redis, time, max_wait=0)

    await replica_a.complete(HELLO)
    await replica_b.complete(HELLO)
    with pytest.raises(LLMOverloadedError) as info:
        await replica_a.complete(HELLO)

    assert info.value.retry_after == pytest.approx(30.0)


async def test_shared_quota_waits_when_it_fits_in_max_wait() -> None:
    time = FakeTime()
    llm = _shared(InMemoryRateLimiter(clock=time.clock), time, rpm=60, max_wait=5.0)
    for _ in range(60):
        await llm.complete(HELLO)

    await llm.complete(HELLO)

    assert len(time.sleeps) == 1
    assert 1.0 <= time.sleeps[0] <= 1.1  # Lo que pide el limitador, con un poco de jitter.


async def test_shared_quota_respects_the_time_budget() -> None:
    time = FakeTime()
    llm = _shared(InMemoryRateLimiter(clock=time.clock), time, rpm=1)
    await llm.complete(HELLO)

    with time_budget(10.0, clock=time.clock), pytest.raises(LLMOverloadedError):
        await llm.complete(HELLO)  # Necesitaría 60 s.
    assert time.sleeps == []


async def test_shared_quota_fails_open_when_the_store_is_down() -> None:
    inner = FakeLLM()
    llm = SharedQuotaLLM(
        inner, DownLimiter(), key="azure:strong", requests_per_minute=1, tokens_per_minute=1
    )

    await llm.complete(HELLO)
    await llm.complete(HELLO)

    assert len(inner.calls) == 2


# --- Admisión ----------------------------------------------------------------------------


def _controller(capacity: int = 10, **kwargs: object) -> AdmissionController:
    time = FakeTime()
    return AdmissionController(
        InMemoryRateLimiter(clock=time.clock),
        rules=plan_rules({ClientPlan.ANONYMOUS: 2, ClientPlan.PUBLIC: 3, ClientPlan.INTERNAL: 100}),
        capacity=capacity,
        **kwargs,  # type: ignore[arg-type]
    )


def test_internal_keeps_capacity_that_public_cannot_use() -> None:
    assert has_capacity(ClientPlan.PUBLIC, in_flight=7, capacity=10)
    assert not has_capacity(ClientPlan.PUBLIC, in_flight=8, capacity=10)
    assert has_capacity(ClientPlan.INTERNAL, in_flight=9, capacity=10)
    assert not has_capacity(ClientPlan.ANONYMOUS, in_flight=6, capacity=10)


def test_only_internal_staff_see_internal_documents() -> None:
    assert access_scope(ClientPlan.INTERNAL).allowed_acls == ("public", "internal")
    assert access_scope(ClientPlan.PUBLIC).allowed_acls == ("public",)
    assert access_scope(ClientPlan.ANONYMOUS).allowed_acls == ("public",)


async def test_admission_rate_limits_per_user_and_plan() -> None:
    controller = _controller()
    ana = Caller("sub:ana", ClientPlan.PUBLIC)
    for expected_remaining in (2, 1, 0):
        async with controller.admit(ana) as admission:
            assert admission.remaining == expected_remaining

    with pytest.raises(CallerRateLimitedError) as info:
        async with controller.admit(ana):
            pass

    assert info.value.status == 429
    assert info.value.rule.name == "user_public"
    assert controller.outcomes == {"admitted": 3, "rate_limited": 1}


async def test_shedding_rejects_public_but_lets_internal_in() -> None:
    controller = _controller(capacity=5)
    release = asyncio.Event()
    entered = 0

    async def hold(caller: Caller) -> None:
        nonlocal entered
        async with controller.admit(caller):
            entered += 1
            await release.wait()

    holders = [asyncio.create_task(hold(Caller(f"sub:u{i}", ClientPlan.PUBLIC))) for i in range(4)]
    await asyncio.sleep(0)
    assert controller.in_flight == 4

    with pytest.raises(ServiceOverloadedError) as info:
        async with controller.admit(Caller("sub:late", ClientPlan.PUBLIC)):
            pass
    async with controller.admit(Caller("sub:ops", ClientPlan.INTERNAL)):
        pass
    release.set()
    await asyncio.gather(*holders)

    assert info.value.status == 503
    assert (entered, controller.in_flight) == (4, 0)
    assert controller.outcomes["shed"] == 1


async def test_slot_is_released_when_the_request_fails() -> None:
    controller = _controller()

    with pytest.raises(RuntimeError):
        async with controller.admit(Caller("sub:ana", ClientPlan.PUBLIC)):
            raise RuntimeError("fallo del asistente")

    assert controller.in_flight == 0


async def test_rejected_by_rate_limit_does_not_keep_the_slot() -> None:
    controller = _controller()
    anon = Caller("ip:1.2.3.4", ClientPlan.ANONYMOUS)
    for _ in range(2):
        async with controller.admit(anon):
            pass

    with pytest.raises(CallerRateLimitedError):
        async with controller.admit(anon):
            pass

    assert controller.in_flight == 0


async def test_falls_back_to_a_local_limit_when_redis_is_down() -> None:
    controller = AdmissionController(
        DownLimiter(),
        rules=plan_rules({ClientPlan.PUBLIC: 1}),
        capacity=10,
        fallback=InMemoryRateLimiter(),
    )
    ana = Caller("sub:ana", ClientPlan.PUBLIC)

    async with controller.admit(ana):
        pass
    with pytest.raises(CallerRateLimitedError):
        async with controller.admit(ana):
            pass
    assert controller.outcomes["limiter_unavailable"] == 2


async def test_without_fallback_a_limiter_outage_is_an_error() -> None:
    controller = AdmissionController(
        DownLimiter(), rules=plan_rules({ClientPlan.PUBLIC: 1}), capacity=10
    )

    with pytest.raises(RateLimiterUnavailableError):
        async with controller.admit(Caller("sub:ana", ClientPlan.PUBLIC)):
            pass
    assert controller.in_flight == 0

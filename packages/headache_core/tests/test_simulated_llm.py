"""Proveedor simulado (fase 10.7): latencia, cuota con 429 y el gateway completo encima."""

from __future__ import annotations

import asyncio
import random
import time

import pytest

from headache_core.adapters.llm_factory import build_gateway
from headache_core.adapters.memory import InMemoryRateLimiter
from headache_core.adapters.simulated_llm import STATS, SimulatedLLM
from headache_core.application.llm.streaming import collect
from headache_core.config import LLMProvider, Settings, SimulatedLLMSettings
from headache_core.ports import (
    ChatMessage,
    LLMOverloadedError,
    LLMRateLimitError,
    LLMTransientError,
    Role,
    TextDelta,
)

HELLO = [ChatMessage(Role.USER, "¿A qué hora abren las puertas?")]
FAST = SimulatedLLMSettings(ttft_ms=1, tokens_per_second=10_000, output_tokens=20)


async def test_streams_tokens_and_cites_the_first_source() -> None:
    llm = SimulatedLLM(FAST, rng=random.Random(1))

    events = [e async for e in llm.stream(HELLO)]

    deltas = [e for e in events if isinstance(e, TextDelta)]
    final = events[-1]
    assert len(deltas) == 19
    assert not isinstance(final, TextDelta)
    assert final.content.endswith("[1].")
    assert final.usage.output_tokens == 20
    assert final.model == "gpt-4.1-mini-simulated"  # Se cotiza como gpt-4.1-mini.


async def test_latency_is_time_to_first_token_plus_generation() -> None:
    settings = SimulatedLLMSettings(ttft_ms=100, tokens_per_second=200, output_tokens=20)
    llm = SimulatedLLM(settings, rng=random.Random(3))

    started = time.perf_counter()
    first = None
    async for _event in llm.stream(HELLO):
        if first is None:
            first = time.perf_counter() - started
    total = time.perf_counter() - started

    assert first is not None and 0.05 < first < 0.35
    assert total > first + 0.08  # ~20 tokens a 200/s.


async def test_quota_exhaustion_is_a_429_with_retry_after() -> None:
    settings = SimulatedLLMSettings(
        ttft_ms=1, tokens_per_second=10_000, output_tokens=10, requests_per_minute=2
    )
    llm = SimulatedLLM(settings, quota=InMemoryRateLimiter())
    before = STATS["rate_limited"]

    await llm.complete(HELLO)
    await llm.complete(HELLO)
    with pytest.raises(LLMRateLimitError) as info:
        await llm.complete(HELLO)

    assert info.value.retry_after is not None and 25 < info.value.retry_after <= 30
    assert STATS["rate_limited"] == before + 1


async def test_random_server_errors() -> None:
    settings = SimulatedLLMSettings(ttft_ms=1, tokens_per_second=10_000, error_rate=1.0)

    with pytest.raises(LLMTransientError, match="503"):
        await SimulatedLLM(settings).complete(HELLO)


async def test_the_full_gateway_turns_provider_429s_into_fast_rejections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Cuota del proveedor de 3 peticiones por minuto: el resto debe salir rápido del gateway
    # (breaker abierto por el Retry-After), no colgarse ni martillear al proveedor.
    monkeypatch.setenv("LLM_PROVIDER", "simulated")
    monkeypatch.setenv("SIMULATED_LLM__TTFT_MS", "1")
    monkeypatch.setenv("SIMULATED_LLM__TOKENS_PER_SECOND", "10000")
    monkeypatch.setenv("SIMULATED_LLM__REQUESTS_PER_MINUTE", "3")
    monkeypatch.setenv("LLM_GATEWAY__REQUESTS_PER_MINUTE", "1000")
    monkeypatch.setenv("LLM_GATEWAY__TOKENS_PER_MINUTE", "10000000")
    settings = Settings(_env_file=None)
    assert settings.llm_provider is LLMProvider.SIMULATED
    gateway = build_gateway(settings)
    before = STATS["rate_limited"]

    results = await asyncio.gather(
        *(collect(gateway.stream(HELLO)) for _ in range(10)),  # type: ignore[attr-defined]
        return_exceptions=True,
    )

    ok = [r for r in results if not isinstance(r, BaseException)]
    failed = [r for r in results if isinstance(r, BaseException)]
    assert len(ok) == 3
    assert all(isinstance(f, LLMTransientError) for f in failed)
    # Tras el primer 429 el circuito se abre: el proveedor ve muy pocas peticiones más.
    assert STATS["rate_limited"] - before <= 2
    assert any(isinstance(f.__cause__, LLMOverloadedError) for f in failed)

"""LLM Gateway propio (carril B): decoradores que envuelven cualquier `LLMPort`.

Se componen como capas de cebolla, igual que las políticas de APIM:
    RoutingLLM -> FallbackLLM -> ObservedLLM -> RateLimitedLLM -> adaptador (Azure / Bedrock)
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import Decimal

from headache_core.application.llm.pricing import PriceTable
from headache_core.ports import (
    ChatMessage,
    GenerationObservation,
    LLMPort,
    LLMRateLimitError,
    LLMResponse,
    LLMTracer,
    LLMTransientError,
    ResponseSchema,
    ToolConfig,
)

type Sleep = Callable[[float], Awaitable[None]]
type Clock = Callable[[], float]


class FallbackLLM:
    """Prueba los proveedores en orden. Reintenta los fallos transitorios con backoff.

    Un 429 no se reintenta contra el mismo proveedor (su cuota sigue agotada): se salta al
    siguiente. Los errores no transitorios (400, credenciales) se propagan sin más.
    """

    def __init__(
        self,
        candidates: Sequence[LLMPort],
        *,
        max_retries: int = 2,
        base_delay: float = 0.5,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not candidates:
            raise ValueError("FallbackLLM necesita al menos un proveedor.")
        self._candidates = list(candidates)
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._sleep = sleep

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        last_error: LLMTransientError | None = None
        for llm in self._candidates:
            for attempt in range(self._max_retries + 1):
                try:
                    return await llm.complete(
                        messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_schema=response_schema,
                        tools=tools,
                    )
                except LLMRateLimitError as exc:
                    last_error = exc
                    break
                except LLMTransientError as exc:
                    last_error = exc
                    if attempt < self._max_retries:
                        # Full jitter: evita que todos los clientes reintenten a la vez.
                        await self._sleep(random.uniform(0, self._base_delay * 2**attempt))
        raise LLMTransientError("Todos los proveedores han fallado.") from last_error


class TokenBucket:
    """Cubo de fichas: `capacity` de ráfaga y recarga continua de `refill_per_second`."""

    def __init__(
        self,
        capacity: float,
        refill_per_second: float,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._capacity = capacity
        self._rate = refill_per_second
        self._tokens = capacity
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    async def acquire(self, amount: float = 1.0) -> float:
        """Espera hasta tener `amount` fichas. Devuelve los segundos esperados."""
        amount = min(amount, self._capacity)
        waited = 0.0
        async with self._lock:
            while True:
                now = self._clock()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated) * self._rate
                )
                self._updated = now
                if self._tokens >= amount:
                    self._tokens -= amount
                    return waited
                delay = (amount - self._tokens) / self._rate
                waited += delay
                await self._sleep(delay)


def estimate_tokens(messages: Sequence[ChatMessage]) -> int:
    """Aproximación barata (~4 caracteres por token) para reservar cuota antes de llamar."""
    chars = sum(len(m.content) + sum(len(c.arguments) for c in m.tool_calls) for m in messages)
    return chars // 4 + 1


class RateLimitedLLM:
    """Límite en cliente (RPM y TPM) para no provocar 429 en la cuota del despliegue.

    Es por proceso: con varias réplicas el límite debe ser distribuido (Redis, APIM
    `azure-openai-token-limit`, LiteLLM Proxy).
    """

    def __init__(
        self,
        inner: LLMPort,
        *,
        requests_per_minute: int,
        tokens_per_minute: int,
        default_max_tokens: int = 1024,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._requests = TokenBucket(
            requests_per_minute, requests_per_minute / 60, clock=clock, sleep=sleep
        )
        self._tokens = TokenBucket(
            tokens_per_minute, tokens_per_minute / 60, clock=clock, sleep=sleep
        )
        self._default_max_tokens = default_max_tokens

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        await self._requests.acquire()
        await self._tokens.acquire(
            estimate_tokens(messages) + (max_tokens or self._default_max_tokens)
        )
        return await self._inner.complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=response_schema,
            tools=tools,
        )


class ObservedLLM:
    """Traza cada llamada (una *generation*) con tokens, caché, coste y latencia."""

    def __init__(
        self,
        inner: LLMPort,
        *,
        tracer: LLMTracer,
        prices: PriceTable,
        name: str,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        self._inner = inner
        self._tracer = tracer
        self._prices = prices
        self._name = name
        self._metadata = dict(metadata or {})

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        metadata = self._metadata
        if response_schema is not None:
            metadata = {**metadata, "response_schema": response_schema.name}
        if tools is not None:
            metadata = {**metadata, "tools": ",".join(t.name for t in tools.tools)}
        observation = self._tracer.start_generation(
            name=self._name, messages=messages, metadata=metadata
        )
        try:
            response = await self._inner.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_schema=response_schema,
                tools=tools,
            )
        except Exception as exc:
            observation.fail(exc)
            raise
        observation.finish(response, cost_usd=self._prices.cost(response.model, response.usage))
        return response


class _NullObservation:
    def finish(self, response: LLMResponse, *, cost_usd: Decimal | None) -> None:
        pass

    def fail(self, error: BaseException) -> None:
        pass


class NullTracer:
    def start_generation(
        self, *, name: str, messages: Sequence[ChatMessage], metadata: Mapping[str, str]
    ) -> GenerationObservation:
        return _NullObservation()


class _CompositeObservation:
    def __init__(self, observations: Sequence[GenerationObservation]) -> None:
        self._observations = observations

    def finish(self, response: LLMResponse, *, cost_usd: Decimal | None) -> None:
        for observation in self._observations:
            observation.finish(response, cost_usd=cost_usd)

    def fail(self, error: BaseException) -> None:
        for observation in self._observations:
            observation.fail(error)


class CompositeTracer:
    """Envía cada generation a varios destinos (p. ej. Langfuse + contabilidad en memoria)."""

    def __init__(self, tracers: Sequence[LLMTracer]) -> None:
        self._tracers = list(tracers)

    def start_generation(
        self, *, name: str, messages: Sequence[ChatMessage], metadata: Mapping[str, str]
    ) -> GenerationObservation:
        return _CompositeObservation(
            [
                t.start_generation(name=name, messages=messages, metadata=metadata)
                for t in self._tracers
            ]
        )

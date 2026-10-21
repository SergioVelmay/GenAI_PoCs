"""LLM Gateway propio (carril B): decoradores que envuelven cualquier `LLMPort`.

Se componen como capas de cebolla, igual que las políticas de APIM:
    RoutingLLM -> FallbackLLM -> CircuitBreakerLLM -> ConcurrencyLimitedLLM -> ObservedLLM
    -> SharedQuotaLLM (cuota entre réplicas, 10.2) -> RateLimitedLLM -> adaptador
El breaker y el bulkhead están en `resilience.py` (fase 10.1).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from decimal import Decimal

from headache_core.application.llm.pricing import PriceTable
from headache_core.application.llm.resilience import bounded_wait, remaining
from headache_core.application.llm.streaming import stream_from, with_deadline
from headache_core.ports import (
    ChatMessage,
    GenerationObservation,
    LLMDeadlineExceededError,
    LLMOverloadedError,
    LLMPort,
    LLMRateLimitError,
    LLMResponse,
    LLMStreamEvent,
    LLMTracer,
    LLMTransientError,
    RateLimiterPort,
    RateLimiterUnavailableError,
    RateLimitRequest,
    RateLimitRule,
    ResponseSchema,
    TextDelta,
    ToolConfig,
)

logger = logging.getLogger(__name__)

type Sleep = Callable[[float], Awaitable[None]]
type Clock = Callable[[], float]


class FallbackLLM:
    """Prueba los proveedores en orden. Reintenta los fallos transitorios con backoff.

    - Un 429 o un rechazo local (`LLMOverloadedError`: circuito abierto, sin cuota en el
      cliente) no se reintenta contra el mismo proveedor: se salta al siguiente.
    - Un 5xx con `Retry-After` espera al menos eso; si pide más de `max_retry_after`, se salta.
    - Con presupuesto de tiempo (`time_budget`) no se espera más de lo que queda y, si se agota,
      `LLMDeadlineExceededError` sin más intentos: el usuario ya no está esperando.
    - Los errores no transitorios (400, credenciales) se propagan sin más.
    - Si todos fallan, el error final lleva el `retry_after` más corto que se conozca.
    """

    def __init__(
        self,
        candidates: Sequence[LLMPort],
        *,
        max_retries: int = 2,
        base_delay: float = 0.5,
        max_retry_after: float = 5.0,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        if not candidates:
            raise ValueError("FallbackLLM necesita al menos un proveedor.")
        self._candidates = list(candidates)
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_retry_after = max_retry_after
        self._sleep = sleep
        self._clock = clock

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
        hints: list[float] = []
        for llm in self._candidates:
            for attempt in range(self._max_retries + 1):
                self._check_deadline(last_error)
                try:
                    return await self._call(
                        llm, messages, temperature, max_tokens, response_schema, tools
                    )
                except LLMDeadlineExceededError:
                    raise
                except LLMTransientError as exc:
                    last_error = exc
                    delay = self._retry_delay(exc, attempt, hints)
                    if delay is None:
                        break
                    await self._sleep(delay)
        raise self._exhausted(hints) from last_error

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        """Igual que `complete`, pero solo se reintenta o se salta si aún no se ha emitido nada."""
        last_error: LLMTransientError | None = None
        hints: list[float] = []
        for llm in self._candidates:
            for attempt in range(self._max_retries + 1):
                self._check_deadline(last_error)
                emitted = False
                events = with_deadline(
                    stream_from(llm, messages, temperature=temperature, max_tokens=max_tokens),
                    remaining(clock=self._clock),
                )
                try:
                    async for event in events:
                        emitted = True
                        yield event
                    return
                except LLMDeadlineExceededError:
                    raise
                except LLMTransientError as exc:
                    if emitted:
                        raise
                    last_error = exc
                    delay = self._retry_delay(exc, attempt, hints)
                    if delay is None:
                        break
                    await self._sleep(delay)
        raise self._exhausted(hints) from last_error

    def _retry_delay(
        self, exc: LLMTransientError, attempt: int, hints: list[float]
    ) -> float | None:
        """Cuánto esperar antes de reintentar con el mismo proveedor, o `None` para saltar."""
        if exc.retry_after:
            hints.append(exc.retry_after)
        if isinstance(exc, LLMRateLimitError | LLMOverloadedError) or attempt == self._max_retries:
            return None
        # Full jitter: evita que todos los clientes reintenten a la vez.
        delay = max(random.uniform(0, self._base_delay * 2**attempt), exc.retry_after or 0.0)
        left = remaining(clock=self._clock)
        if delay > self._max_retry_after or (left is not None and delay >= left):
            return None
        return delay

    @staticmethod
    def _exhausted(hints: list[float]) -> LLMTransientError:
        return LLMTransientError(
            "Todos los proveedores han fallado.", retry_after=min(hints, default=None)
        )

    def _check_deadline(self, last_error: LLMTransientError | None) -> None:
        left = remaining(clock=self._clock)
        if left is not None and left <= 0:
            raise LLMDeadlineExceededError(
                "Presupuesto de tiempo agotado antes de llamar al modelo."
            ) from last_error

    async def _call(
        self,
        llm: LLMPort,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int | None,
        response_schema: ResponseSchema | None,
        tools: ToolConfig | None,
    ) -> LLMResponse:
        timeout = asyncio.timeout(remaining(clock=self._clock))
        try:
            async with timeout:
                return await llm.complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_schema=response_schema,
                    tools=tools,
                )
        except TimeoutError as exc:
            if not timeout.expired():
                raise
            raise LLMDeadlineExceededError("Presupuesto de tiempo agotado.") from exc


class WaitTooLongError(Exception):
    """Conseguir las fichas exigiría esperar más de lo permitido."""

    def __init__(self, wait: float) -> None:
        super().__init__(f"Habría que esperar {wait:.2f} s.")
        self.wait = wait


class TokenBucket:
    """Cubo de fichas: `capacity` de ráfaga y recarga continua de `refill_per_second`.

    Funciona por **reservas**: cada llamante descuenta sus fichas al llegar (el saldo puede
    quedar negativo, es deuda) y duerme lo que le toca. Así cada uno conoce su espera exacta
    antes de dormir y puede rechazar en vez de quedarse en una cola sin fondo (`max_wait`).
    """

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

    def reserve(self, amount: float = 1.0, *, max_wait: float | None = None) -> float:
        """Reserva `amount` fichas y devuelve cuánto hay que esperar para usarlas.

        Sin `await` entre leer y descontar: en asyncio es atómico sin candado.
        """
        amount = min(amount, self._capacity)
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now
        wait = max(0.0, (amount - self._tokens) / self._rate)
        if max_wait is not None and wait > max_wait:
            raise WaitTooLongError(wait)
        self._tokens -= amount
        return wait

    def refund(self, amount: float = 1.0) -> None:
        """Devuelve una reserva que no se va a usar (rechazo o cancelación)."""
        self._tokens = min(self._capacity, self._tokens + min(amount, self._capacity))

    async def acquire(self, amount: float = 1.0, *, max_wait: float | None = None) -> float:
        """Espera hasta tener `amount` fichas. Devuelve los segundos esperados."""
        wait = self.reserve(amount, max_wait=max_wait)
        if wait > 0:
            try:
                await self._sleep(wait)
            except asyncio.CancelledError:
                self.refund(amount)
                raise
        return wait


def estimate_tokens(messages: Sequence[ChatMessage]) -> int:
    """Aproximación barata (~4 caracteres por token) para reservar cuota antes de llamar."""
    chars = sum(len(m.content) + sum(len(c.arguments) for c in m.tool_calls) for m in messages)
    return chars // 4 + 1


class RateLimitedLLM:
    """Límite en cliente (RPM y TPM) para no provocar 429 en la cuota del despliegue.

    Es por proceso: con varias réplicas el límite debe ser distribuido (Redis, APIM
    `azure-openai-token-limit`, LiteLLM Proxy).

    Con `max_wait` (o con presupuesto de tiempo), si la cuota no llega a tiempo rechaza con
    `LLMOverloadedError` y su `retry_after` en vez de esperar: eso es *backpressure*.
    """

    def __init__(
        self,
        inner: LLMPort,
        *,
        requests_per_minute: int,
        tokens_per_minute: int,
        default_max_tokens: int = 1024,
        max_wait: float | None = None,
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
        self._max_wait = max_wait
        self._clock = clock
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
        await self._reserve(estimate_tokens(messages) + (max_tokens or self._default_max_tokens))
        return await self._inner.complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=response_schema,
            tools=tools,
        )

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        # Se reserva lo mismo que sin streaming: la cuota es del modelo, no de la forma de leer.
        await self._reserve(estimate_tokens(messages) + (max_tokens or self._default_max_tokens))
        async for event in stream_from(
            self._inner, messages, temperature=temperature, max_tokens=max_tokens
        ):
            yield event

    async def _reserve(self, tokens: int) -> None:
        """Reserva petición y tokens a la vez (o ninguna) y duerme una sola vez."""
        max_wait = bounded_wait(self._max_wait, clock=self._clock)
        try:
            request_wait = self._requests.reserve(1, max_wait=max_wait)
            try:
                token_wait = self._tokens.reserve(tokens, max_wait=max_wait)
            except WaitTooLongError:
                self._requests.refund(1)
                raise
        except WaitTooLongError as exc:
            raise LLMOverloadedError(
                "Cuota del modelo agotada en el cliente.", retry_after=exc.wait
            ) from exc
        wait = max(request_wait, token_wait)
        if wait > 0:
            try:
                await self._sleep(wait)
            except asyncio.CancelledError:
                self._requests.refund(1)
                self._tokens.refund(tokens)
                raise


class SharedQuotaLLM:
    """Cuota del despliegue (RPM y TPM) compartida entre réplicas mediante `RateLimiterPort`.

    `RateLimitedLLM` limita cada proceso; con 10 réplicas, 10 veces la cuota llega al
    proveedor y vuelven los 429. Aquí todas descuentan del mismo contador (Redis), de forma
    atómica: petición y tokens a la vez o nada.

    - Si no hay cuota y cabe esperar (`max_wait` o el presupuesto), duerme lo que dice el
      limitador (con algo de *jitter* para no despertar todas a la vez) y vuelve a pedir.
    - Si no cabe, `LLMOverloadedError` con `retry_after`: el fallback salta de proveedor.
    - Si el limitador cae, *fail-open*: queda el `RateLimitedLLM` local como red.
    """

    def __init__(
        self,
        inner: LLMPort,
        limiter: RateLimiterPort,
        *,
        key: str,
        requests_per_minute: int,
        tokens_per_minute: int,
        default_max_tokens: int = 1024,
        max_wait: float | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._limiter = limiter
        self._key = key
        self._rpm = RateLimitRule("model_rpm", requests_per_minute, 60.0)
        self._tpm = RateLimitRule("model_tpm", tokens_per_minute, 60.0)
        self._default_max_tokens = default_max_tokens
        self._max_wait = max_wait
        self._clock = clock
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
        tokens = estimate_tokens(messages) + (max_tokens or self._default_max_tokens)
        await self._reserve(tokens)
        return await self._inner.complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=response_schema,
            tools=tools,
        )

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        await self._reserve(estimate_tokens(messages) + (max_tokens or self._default_max_tokens))
        async for event in stream_from(
            self._inner, messages, temperature=temperature, max_tokens=max_tokens
        ):
            yield event

    async def _reserve(self, tokens: int) -> None:
        requests = [
            RateLimitRequest(f"model:{self._key}:rpm", self._rpm),
            RateLimitRequest(f"model:{self._key}:tpm", self._tpm, tokens),
        ]
        started = self._clock()
        while True:
            try:
                decision = await self._limiter.acquire(requests)
            except RateLimiterUnavailableError:
                logger.warning("Cuota compartida no disponible (%s): fail-open.", self._key)
                return
            if decision.allowed:
                return
            waited = self._clock() - started
            left = remaining(clock=self._clock)
            if (self._max_wait is not None and waited + decision.retry_after > self._max_wait) or (
                left is not None and decision.retry_after > left
            ):
                raise LLMOverloadedError(
                    f"Cuota compartida agotada ({decision.limited_by}).",
                    retry_after=decision.retry_after,
                )
            await self._sleep(decision.retry_after * random.uniform(1.0, 1.1))


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

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        observation = self._tracer.start_generation(
            name=self._name, messages=messages, metadata={**self._metadata, "stream": "true"}
        )
        finished = False
        first = True
        try:
            async for event in stream_from(
                self._inner, messages, temperature=temperature, max_tokens=max_tokens
            ):
                if first and isinstance(event, TextDelta):
                    observation.first_token()
                    first = False
                if isinstance(event, LLMResponse):
                    cost = self._prices.cost(event.model, event.usage)
                    observation.finish(event, cost_usd=cost)
                    finished = True
                yield event
        except BaseException as exc:
            # También si el cliente corta el stream a medias (GeneratorExit / cancelación): la
            # traza queda como fallida en vez de abierta. Los tokens ya generados se pagan igual.
            if not finished:
                observation.fail(exc)
            raise


class _NullObservation:
    def first_token(self) -> None:
        pass

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

    def first_token(self) -> None:
        for observation in self._observations:
            observation.first_token()

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

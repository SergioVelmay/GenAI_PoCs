"""Resiliencia del cliente LLM bajo carga (fase 10.1).

- `time_budget` / `remaining`: presupuesto de tiempo de la petición (*deadline propagation*).
  Nadie reintenta ni espera cuota más allá de lo que el usuario va a esperar.
- `CircuitBreakerLLM`: deja de llamar a un proveedor que falla y vuelve a probar con una sola
  llamada (cerrado → abierto → semiabierto). Equivale al *circuit breaker* de backends de APIM.
- `ConcurrencyLimitedLLM`: *bulkhead*; acota las llamadas en vuelo por despliegue y rechaza en
  vez de encolar sin límite (una cola infinita solo convierte la sobrecarga en timeouts).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum

from headache_core.application.llm.streaming import stream_from
from headache_core.application.telemetry import metrics
from headache_core.ports import (
    ChatMessage,
    LLMDeadlineExceededError,
    LLMError,
    LLMOverloadedError,
    LLMPort,
    LLMRateLimitError,
    LLMResponse,
    LLMStreamEvent,
    LLMTransientError,
    ResponseSchema,
    ToolConfig,
)

logger = logging.getLogger(__name__)

type Clock = Callable[[], float]

_deadline: ContextVar[float | None] = ContextVar("llm_deadline", default=None)


@contextmanager
def time_budget(seconds: float, *, clock: Clock = time.monotonic) -> Iterator[None]:
    """Fija el tiempo máximo de la petición en curso. Anidados, manda el más estricto."""
    deadline = clock() + seconds
    current = _deadline.get()
    token = _deadline.set(deadline if current is None else min(current, deadline))
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining(*, clock: Clock = time.monotonic) -> float | None:
    """Segundos que quedan del presupuesto; `None` si la petición no tiene presupuesto."""
    deadline = _deadline.get()
    return None if deadline is None else deadline - clock()


def bounded_wait(max_wait: float | None, *, clock: Clock = time.monotonic) -> float | None:
    """La espera máxima permitida: la configurada, recortada por el presupuesto restante."""
    left = remaining(clock=clock)
    if left is None:
        return max_wait
    left = max(0.0, left)
    return left if max_wait is None else min(max_wait, left)


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerLLM:
    """Abre el circuito tras `failure_threshold` fallos transitorios seguidos.

    - Abierto: rechaza al instante con `LLMOverloadedError` (sin coste ni latencia) y el
      `FallbackLLM` salta al siguiente proveedor.
    - Pasado `reset_timeout`: semiabierto, deja pasar **una** llamada de prueba; si va bien se
      cierra, si falla vuelve a abrirse.
    - Un 429 con `Retry-After` abre el circuito ese tiempo a la primera: el proveedor ya ha dicho
      cuándo volver (como `acceptRetryAfter` en APIM).
    - Los errores no transitorios (400, filtro de contenido) son un proveedor sano: no cuentan.
      Los rechazos locales (cuota del cliente, presupuesto agotado) tampoco.

    El estado es por proceso: cada réplica aprende sola, que es lo habitual.
    """

    def __init__(
        self,
        inner: LLMPort,
        *,
        name: str,
        failure_threshold: int = 5,
        reset_timeout: float = 30.0,
        clock: Clock = time.monotonic,
        on_transition: Callable[[str, CircuitState], None] | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold debe ser >= 1.")
        self._inner = inner
        self._name = name
        self._threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._clock = clock
        self._on_transition = on_transition
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._open_until = 0.0
        self._probing = False

    @property
    def state(self) -> CircuitState:
        if self._state is CircuitState.OPEN and self._clock() >= self._open_until:
            self._transition(CircuitState.HALF_OPEN)
        return self._state

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        probe = self._admit()
        try:
            response = await self._inner.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_schema=response_schema,
                tools=tools,
            )
        except BaseException as exc:
            self._on_error(exc, probe)
            raise
        self._record_success()
        return response

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        """Un fallo a mitad del stream cuenta como fallo; el éxito, al llegar la respuesta final."""
        probe = self._admit()
        try:
            async for event in stream_from(
                self._inner, messages, temperature=temperature, max_tokens=max_tokens
            ):
                if isinstance(event, LLMResponse):
                    self._record_success()
                yield event
        except BaseException as exc:
            self._on_error(exc, probe)
            raise

    def _on_error(self, exc: BaseException, probe: bool) -> None:
        if isinstance(exc, LLMOverloadedError | LLMDeadlineExceededError):
            self._release(probe)
        elif isinstance(exc, LLMRateLimitError):
            if exc.retry_after:
                self._open(exc.retry_after)
            else:
                self._record_failure(probe)
        elif isinstance(exc, LLMTransientError):
            self._record_failure(probe)
        elif isinstance(exc, LLMError):
            self._record_success()
        else:
            # Cancelación, cliente que corta el stream o fallo propio: nada sobre el proveedor.
            self._release(probe)

    def _admit(self) -> bool:
        """Deja pasar la llamada o la rechaza. Devuelve si es la llamada de prueba."""
        state = self.state
        if state is CircuitState.CLOSED:
            return False
        if state is CircuitState.HALF_OPEN and not self._probing:
            self._probing = True
            return True
        retry_after = max(self._open_until - self._clock(), 0.0) or None
        metrics.llm_rejected(deployment=self._name, reason="circuit_open")
        raise LLMOverloadedError(f"Circuito abierto: {self._name}.", retry_after=retry_after)

    def _release(self, probe: bool) -> None:
        if probe:
            self._probing = False

    def _record_success(self) -> None:
        self._failures = 0
        self._probing = False
        if self._state is not CircuitState.CLOSED:
            self._transition(CircuitState.CLOSED)

    def _record_failure(self, probe: bool) -> None:
        self._failures += 1
        if probe or self._failures >= self._threshold:
            self._open(self._reset_timeout)

    def _open(self, seconds: float) -> None:
        self._probing = False
        self._open_until = max(self._open_until, self._clock() + seconds)
        if self._state is not CircuitState.OPEN:
            self._transition(CircuitState.OPEN)

    def _transition(self, state: CircuitState) -> None:
        self._state = state
        if state is CircuitState.CLOSED:
            self._open_until = 0.0
        logger.warning("Circuito %s: %s", self._name, state)
        if self._on_transition is not None:
            self._on_transition(self._name, state)


class ConcurrencyLimitedLLM:
    """*Bulkhead*: como mucho `max_concurrency` llamadas en vuelo a este despliegue.

    Si no hay hueco en `max_wait` segundos (o en lo que quede del presupuesto), rechaza con
    `LLMOverloadedError`. Así la memoria y las conexiones quedan acotadas y la latencia de cola
    no crece sin límite (ley de Little: en vuelo = llegadas x latencia).
    """

    def __init__(
        self,
        inner: LLMPort,
        *,
        max_concurrency: int,
        max_wait: float | None = None,
        clock: Clock = time.monotonic,
        name: str = "",
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency debe ser >= 1.")
        self._name = name
        self._inner = inner
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_wait = max_wait
        self._clock = clock
        self.in_flight = 0
        self.waiting = 0
        self.rejected = 0

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        await self._acquire()
        self.in_flight += 1
        try:
            return await self._inner.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_schema=response_schema,
                tools=tools,
            )
        finally:
            self.in_flight -= 1
            self._semaphore.release()

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        # El hueco se ocupa mientras dure el stream, no solo hasta el primer trozo: una
        # respuesta en streaming ocupa la conexión con el proveedor todo ese tiempo.
        await self._acquire()
        self.in_flight += 1
        try:
            async for event in stream_from(
                self._inner, messages, temperature=temperature, max_tokens=max_tokens
            ):
                yield event
        finally:
            self.in_flight -= 1
            self._semaphore.release()

    async def _acquire(self) -> None:
        if not self._semaphore.locked():
            await self._semaphore.acquire()  # Hay hueco: no suspende.
            return
        max_wait = bounded_wait(self._max_wait, clock=self._clock)
        if max_wait is not None and max_wait <= 0:
            self._reject()
            raise LLMOverloadedError("Sin hueco de concurrencia para el modelo.")
        self.waiting += 1
        try:
            async with asyncio.timeout(max_wait):
                await self._semaphore.acquire()
        except TimeoutError as exc:
            self._reject()
            raise LLMOverloadedError("Sin hueco de concurrencia para el modelo.") from exc
        finally:
            self.waiting -= 1

    def _reject(self) -> None:
        self.rejected += 1
        metrics.llm_rejected(deployment=self._name, reason="bulkhead")

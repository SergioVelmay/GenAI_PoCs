"""Streaming de respuestas (fase 10.4): utilidades comunes a adaptadores y capas del gateway.

Un stream no cambia lo que mide el gateway (cuota, circuito, coste), solo cuándo llega cada
trozo. Lo que sí cambia es el fallo: **antes del primer trozo** aún se puede reintentar o saltar
de proveedor; **después**, el usuario ya está leyendo una respuesta y empalmar otra sería
mentirle. Por eso el `FallbackLLM` solo cambia de proveedor si no se ha emitido nada.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Sequence

from headache_core.ports import (
    ChatMessage,
    LLMDeadlineExceededError,
    LLMPort,
    LLMResponse,
    LLMStreamEvent,
    StreamingLLMPort,
    TextDelta,
)


async def stream_from(
    llm: LLMPort,
    messages: Sequence[ChatMessage],
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> AsyncGenerator[LLMStreamEvent, None]:
    """El stream del modelo o, si no sabe hacer streaming, su respuesta en un solo trozo."""
    if isinstance(llm, StreamingLLMPort):
        async for event in llm.stream(messages, temperature=temperature, max_tokens=max_tokens):
            yield event
        return
    response = await llm.complete(messages, temperature=temperature, max_tokens=max_tokens)
    if response.content:
        yield TextDelta(response.content)
    yield response


async def with_deadline[T](
    events: AsyncIterator[T], seconds_left: float | None
) -> AsyncGenerator[T, None]:
    """Corta el stream si se agota el presupuesto de tiempo (también entre trozo y trozo).

    Sirve para cualquier stream (del modelo o de la respuesta del edge) y no usa `ContextVar`:
    un stream HTTP empieza en la tarea de la petición y sigue en otra.
    """
    if seconds_left is None:
        async for event in events:
            yield event
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds_left
    iterator = aiter(events)
    try:
        while True:
            try:
                event = await asyncio.wait_for(anext(iterator), max(0.0, deadline - loop.time()))
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                raise LLMDeadlineExceededError(
                    "Presupuesto de tiempo agotado en el stream."
                ) from exc
            yield event
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


async def collect(events: AsyncIterator[LLMStreamEvent]) -> LLMResponse:
    """Consume un stream entero y devuelve la respuesta final (tests y llamantes sin prisa)."""
    final: LLMResponse | None = None
    async for event in events:
        if isinstance(event, LLMResponse):
            final = event
    if final is None:
        raise RuntimeError("El stream terminó sin respuesta final.")
    return final

"""Server-Sent Events del edge (fase 10.4).

Protocolo de `POST /ask/stream` (`text/event-stream`):

    event: delta   data: {"text": "Abren "}            uno por trozo
    event: done    data: {AskOut}                      al final: citas, caché y `verified`
    event: error   data: {"reason": ..., "retry_after"}   si falla a mitad
    : ping                                              latido si no hay datos

Dos momentos de fallo, dos formas de contarlo:
- **Antes del primer trozo**: código HTTP normal (429, 503 + `Retry-After`, 504). El endpoint
  espera al primer evento antes de responder (*priming*) para poder hacerlo.
- **Después**: las cabeceras (200) ya se enviaron; el fallo va como `event: error` y el
  cliente decide (reintentar o enseñar lo que tenía con un aviso).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AsyncExitStack

from fastapi.sse import KEEPALIVE_COMMENT, format_sse_event
from pydantic import BaseModel

from edge_api.assistant import Answer, AnswerChunk, AssistantEvent
from headache_core.application.telemetry import get_telemetry, metrics
from headache_core.ports import LLMDeadlineExceededError, LLMTransientError

logger = logging.getLogger(__name__)

type Pumped = AssistantEvent | None  # None = latido


def sse(event: str, data: object) -> bytes:
    return format_sse_event(event=event, data_str=json.dumps(data, ensure_ascii=False))


async def pump(
    events: AsyncIterator[AssistantEvent], *, budget_seconds: float, heartbeat_seconds: float
) -> AsyncGenerator[Pumped, None]:
    """Itera con presupuesto total de tiempo y emite `None` (latido) si no llega nada.

    La espera de cada evento vive en una tarea propia para no cancelarla en cada latido
    (`wait_for` la cancelaría y rompería el generador del asistente).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_seconds
    iterator = aiter(events)
    pending: asyncio.Future[AssistantEvent] | None = None
    try:
        while True:
            left = deadline - loop.time()
            if left <= 0:
                raise LLMDeadlineExceededError("La respuesta ha tardado demasiado.")
            if pending is None:
                pending = asyncio.ensure_future(anext(iterator))
            done, _ = await asyncio.wait({pending}, timeout=min(heartbeat_seconds, left))
            if not done:
                yield None
                continue
            current, pending = pending, None
            try:
                event = current.result()
            except StopAsyncIteration:
                return
            yield event
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
        close = getattr(iterator, "aclose", None)
        if close is not None:
            with contextlib.suppress(RuntimeError):  # Ya cerrado por la cancelación.
                await close()


async def prime(events: AsyncIterator[Pumped]) -> AssistantEvent:
    """El primer evento real (sin latidos): sus errores aún pueden ser un código HTTP."""
    async for event in events:
        if event is not None:
            return event
    raise RuntimeError("El stream terminó sin eventos.")


type Render = Callable[[Answer], BaseModel]


def _failed(reason: str) -> None:
    # Las cabeceras ya salieron con 200: sin esto, la traza daría por buena una respuesta rota.
    get_telemetry().current_span().mark_error(f"stream_{reason}")
    metrics.stream_error(f"stream_{reason}")


def encode(event: Pumped, *, done: Render) -> bytes:
    if event is None:
        return KEEPALIVE_COMMENT
    if isinstance(event, AnswerChunk):
        return sse("delta", {"text": event.text})
    assert isinstance(event, Answer)
    return sse("done", done(event).model_dump())


async def body(
    first: AssistantEvent,
    events: AsyncGenerator[Pumped, None],
    stack: AsyncExitStack,
    *,
    done: Render,
) -> AsyncGenerator[bytes, None]:
    """El cuerpo del stream. `stack` libera el hueco de admisión al terminar o si el cliente
    se va (Starlette cancela esta tarea y se ejecutan los `finally`)."""
    async with stack:
        try:
            yield encode(first, done=done)
            async for event in events:
                yield encode(event, done=done)
        except LLMDeadlineExceededError:
            _failed("deadline")
            yield sse(
                "error", {"reason": "deadline", "detail": "La respuesta ha tardado demasiado."}
            )
        except LLMTransientError as exc:
            _failed("model_unavailable")
            retry = max(1, math.ceil(exc.retry_after)) if exc.retry_after else 1
            yield sse(
                "error",
                {
                    "reason": "model_unavailable",
                    "detail": "El asistente está saturado.",
                    "retry_after": retry,
                },
            )
        except Exception:
            logger.exception("Fallo a mitad del stream")
            _failed("internal")
            yield sse("error", {"reason": "internal", "detail": "Error interno."})
        finally:
            await events.aclose()

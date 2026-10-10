"""Bucle de tools hecho a mano (carril B): lo que LangGraph, MAF o Strands hacen por dentro.

    modelo -> ¿pide tools? -> validar -> ejecutar (lecturas en paralelo, escrituras en serie)
           -> sanear -> devolver resultados -> modelo ... hasta respuesta final o límite.

El modelo propone; el backend valida, autoriza y ejecuta. Cualquier fallo de una tool vuelve al
modelo como resultado de error (para que corrija o lo explique) y nunca rompe el bucle.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from headache_core.application.tools.registry import ToolContext, ToolError, ToolRegistry
from headache_core.domain.tool_output import render_tool_error, render_tool_output
from headache_core.ports import ChatMessage, LLMPort, LLMResponse, Role, ToolCall

logger = logging.getLogger(__name__)

FALLBACK_ANSWER = (
    "No he podido completar la gestión de forma automática. Un agente de Headache revisará tu "
    "caso y te contestará."
)


class ToolErrorKind(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    TIMEOUT = "timeout"
    TOO_MANY_CALLS = "too_many_calls"
    INTERNAL = "internal_error"


@dataclass(frozen=True, slots=True)
class ToolExecution:
    call: ToolCall
    output: str
    # ToolErrorKind o el `kind` de un ToolError; None si fue bien.
    error: str | None
    latency_ms: float

    @property
    def is_error(self) -> bool:
        return self.error is not None


@dataclass(frozen=True, slots=True)
class ToolLoopResult:
    answer: str
    stop_reason: Literal["answer", "max_iterations"]
    messages: list[ChatMessage]
    # Todas las llamadas al modelo: el coste real de la conversación.
    responses: list[LLMResponse] = field(default_factory=list)
    executions: list[ToolExecution] = field(default_factory=list)


class ToolLoop:
    def __init__(
        self,
        llm: LLMPort,
        registry: ToolRegistry,
        *,
        max_iterations: int = 5,
        max_calls_per_turn: int = 8,
        max_parallel: int = 4,
        max_output_chars: int = 4000,
        parallel_tool_calls: bool = True,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        fallback_answer: str = FALLBACK_ANSWER,
        executor: ToolExecutor | None = None,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._executor = executor or ToolExecutor(registry, max_output_chars=max_output_chars)
        self._max_iterations = max_iterations
        self._max_calls = max_calls_per_turn
        self._max_parallel = max_parallel
        self._config = registry.config(parallel=parallel_tool_calls)
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._fallback = fallback_answer

    async def run(self, messages: Sequence[ChatMessage], context: ToolContext) -> ToolLoopResult:
        conversation = list(messages)
        responses: list[LLMResponse] = []
        executions: list[ToolExecution] = []
        for _ in range(self._max_iterations):
            response = await self._llm.complete(
                conversation,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                tools=self._config,
            )
            responses.append(response)
            if not response.tool_calls:
                conversation.append(ChatMessage(Role.ASSISTANT, response.content))
                return ToolLoopResult(
                    response.content, "answer", conversation, responses, executions
                )
            conversation.append(
                ChatMessage(Role.ASSISTANT, response.content, tool_calls=response.tool_calls)
            )
            turn = await self.execute_turn(response.tool_calls, context)
            executions.extend(turn)
            conversation.extend(
                ChatMessage(Role.TOOL, e.output, tool_call_id=e.call.id, is_error=e.is_error)
                for e in turn
            )
        # Sin respuesta tras el límite: probable bucle. Mejor escalar que seguir gastando.
        return ToolLoopResult(self._fallback, "max_iterations", conversation, responses, executions)

    async def execute_turn(
        self, calls: Sequence[ToolCall], context: ToolContext
    ) -> list[ToolExecution]:
        """Ejecuta las tools de un turno y devuelve sus resultados en el orden de `calls`."""
        results: dict[str, ToolExecution] = {}
        accepted = list(calls[: self._max_calls])
        for call in calls[self._max_calls :]:
            results[call.id] = ToolExecutor.failure(
                call,
                ToolErrorKind.TOO_MANY_CALLS,
                f"Máximo {self._max_calls} llamadas por turno; repite solo las necesarias.",
                time.perf_counter(),
            )

        def is_write(call: ToolCall) -> bool:
            tool = self._registry.get(call.name)
            return tool is not None and not tool.read_only

        reads = [c for c in accepted if not is_write(c)]
        writes = [c for c in accepted if is_write(c)]
        semaphore = asyncio.Semaphore(self._max_parallel)

        async def guarded(call: ToolCall) -> ToolExecution:
            async with semaphore:
                return await self._executor.execute(call, context)

        for execution in await asyncio.gather(*(guarded(c) for c in reads)):
            results[execution.call.id] = execution
        # Escrituras en serie y en el orden pedido: sin carreras entre efectos secundarios.
        for call in writes:
            results[call.id] = await self._executor.execute(call, context)
        return [results[c.id] for c in calls]


class ToolExecutor:
    """Ejecuta UNA llamada: validar, ejecutar con timeout, tipar errores y sanear la salida.

    Lo comparten el bucle local y los servidores MCP: mismas garantías por cualquier transporte.
    """

    def __init__(self, registry: ToolRegistry, *, max_output_chars: int = 4000) -> None:
        self._registry = registry
        self._max_output_chars = max_output_chars

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolExecution:
        started = time.perf_counter()
        tool = self._registry.get(call.name)
        if tool is None:
            return self.failure(
                call,
                ToolErrorKind.UNKNOWN_TOOL,
                f"La tool '{call.name}' no existe. Disponibles: {', '.join(self._registry.names)}.",
                started,
            )
        try:
            args = tool.parse(call.arguments)
        except ValidationError as exc:
            # Sin `input`: no se reenvía al modelo lo que él mismo inventó, solo qué falla.
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or '(raíz)'}: {e['msg']}"
                for e in exc.errors(include_url=False, include_input=False)
            )
            return self.failure(
                call, ToolErrorKind.INVALID_ARGUMENTS, f"Argumentos no válidos: {problems}", started
            )
        try:
            async with asyncio.timeout(tool.timeout_s):
                value = await tool.handler(args, context)
        except TimeoutError:
            if tool.read_only:
                message = f"Sin respuesta en {tool.timeout_s:g} s. Puedes reintentar una vez."
            else:
                message = (
                    "Sin respuesta a tiempo: el resultado es incierto. No repitas la operación; "
                    "indica al cliente que se verificará."
                )
            return self.failure(
                call, ToolErrorKind.TIMEOUT, message, started, retryable=tool.read_only
            )
        except ToolError as exc:
            return self.failure(call, exc.kind, exc.message, started, retryable=exc.retryable)
        except Exception:
            logger.exception("Fallo inesperado en la tool %s", call.name)
            return self.failure(
                call, ToolErrorKind.INTERNAL, "Error interno al ejecutar la tool.", started
            )
        output = render_tool_output(to_jsonable_python(value), max_chars=self._max_output_chars)
        return ToolExecution(call, output, None, _elapsed_ms(started))

    @staticmethod
    def failure(
        call: ToolCall, kind: str, message: str, started: float, *, retryable: bool = False
    ) -> ToolExecution:
        output = render_tool_error(kind, message, retryable=retryable)
        return ToolExecution(call, output, kind, _elapsed_ms(started))


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000

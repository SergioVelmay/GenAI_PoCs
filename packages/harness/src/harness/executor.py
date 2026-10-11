"""Middleware de tools: cada llamada que proponga el modelo pasa por guardas ANTES de ejecutarse.

Funciona con cualquier framework porque todos ejecutan las tools en nuestro `ToolExecutor`
(fase 7): el framework solo ve un resultado de error tipado y el modelo puede corregirse.

`TurnTrace` (contextvar) acumula lo ejecutado en el turno en curso, sin depender del framework:
las tareas asyncio que este cree heredan el contexto. Fuera de un turno (p. ej. un servidor MCP)
no hay traza y las guardas que cuentan por turno solo ven la llamada actual.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from harness.validation import Validator, render_feedback, run_validators
from headache_core.application.tools import (
    Tool,
    ToolContext,
    ToolExecution,
    ToolExecutor,
    ToolRegistry,
)
from headache_core.ports import ToolCall

logger = logging.getLogger(__name__)

POLICY_DENIED = "policy_denied"


@dataclass(slots=True)
class TurnTrace:
    executions: list[ToolExecution] = field(default_factory=list)

    def succeeded(self, tool_name: str) -> list[ToolExecution]:
        return [e for e in self.executions if e.call.name == tool_name and not e.is_error]


_CURRENT_TRACE: ContextVar[TurnTrace | None] = ContextVar("harness_turn_trace", default=None)


@contextmanager
def turn_trace() -> Iterator[TurnTrace]:
    trace = TurnTrace()
    token = _CURRENT_TRACE.set(trace)
    try:
        yield trace
    finally:
        _CURRENT_TRACE.reset(token)


def current_trace() -> TurnTrace | None:
    return _CURRENT_TRACE.get()


@dataclass(frozen=True, slots=True)
class Denial:
    code: str
    # Lo lee el modelo: debe explicarle cómo seguir (corregir, pedir confirmación, escalar).
    message: str


class ToolGuard(Protocol):
    async def check(
        self, tool: Tool[Any], args: BaseModel, context: ToolContext, trace: TurnTrace
    ) -> Denial | None: ...


class GuardedExecutor(ToolExecutor):
    def __init__(
        self,
        registry: ToolRegistry,
        guards: Sequence[ToolGuard],
        *,
        max_output_chars: int = 4000,
    ) -> None:
        super().__init__(registry, max_output_chars=max_output_chars)
        self._registry = registry
        self._guards = tuple(guards)

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolExecution:
        started = time.perf_counter()
        trace = current_trace()
        denial = await self._check(call, context, trace or TurnTrace())
        if denial is not None:
            logger.warning("Tool %s bloqueada por el harness: %s", call.name, denial.code)
            execution = self.failure(call, denial.code, denial.message, started)
        else:
            execution = await super().execute(call, context)
        if trace is not None:
            trace.executions.append(execution)
        return execution

    async def _check(self, call: ToolCall, context: ToolContext, trace: TurnTrace) -> Denial | None:
        tool = self._registry.get(call.name)
        if tool is None:
            return None  # El ejecutor base responde "unknown_tool".
        try:
            args = tool.parse(call.arguments)
        except ValidationError:
            return None  # El ejecutor base devuelve los errores de argumentos al modelo.
        for guard in self._guards:
            denial = await guard.check(tool, args, context, trace)
            if denial is not None:
                return denial
        return None


@dataclass(frozen=True, slots=True)
class MaxCallsPerTurn:
    """Tope de llamadas con éxito por turno, p. ej. un solo reembolso: corta bucles costosos."""

    tools: frozenset[str]
    limit: int = 1

    async def check(
        self, tool: Tool[Any], args: BaseModel, context: ToolContext, trace: TurnTrace
    ) -> Denial | None:
        if tool.name not in self.tools or len(trace.succeeded(tool.name)) < self.limit:
            return None
        return Denial(
            POLICY_DENIED,
            f"Ya se ejecutó {tool.name} en este turno (máximo {self.limit}). No la repitas: "
            "informa al cliente del resultado que ya tienes.",
        )


@dataclass(frozen=True, slots=True)
class ArgumentRules:
    """Reglas extra sobre argumentos ya validados por el esquema (negocio, regex...)."""

    rules: Mapping[str, Sequence[Validator[Any, ToolContext]]]

    async def check(
        self, tool: Tool[Any], args: BaseModel, context: ToolContext, trace: TurnTrace
    ) -> Denial | None:
        violations = await run_validators(self.rules.get(tool.name, ()), args, context)
        if not violations:
            return None
        return Denial(
            POLICY_DENIED,
            "Argumentos rechazados por la política de Headache. Corrígelos:\n"
            + render_feedback(violations),
        )

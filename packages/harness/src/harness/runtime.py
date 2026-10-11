"""Middleware de turno: envuelve CUALQUIER `AgentRuntime` (los siete sabores) sin conocerlo.

    mensaje -> guardas de entrada -> agente (tools vía GuardedExecutor) -> guardas de salida
                    | bloquea                                                 | bloquea
                    v                                                         v
            respuesta segura (no toca el hilo)                 respuesta de escalado

A nivel de turno no se autocorrige: reintentar el turno repetiría efectos y ensuciaría el hilo
con respuestas que el cliente no vio. La autocorrección vive donde es barata y segura: en los
argumentos de las tools (el error vuelve al modelo) y en artefactos (`generate_validated`).
Limitación: el hilo del framework conserva la respuesta bloqueada; el turno se marca
`stop_reason="guardrail"` para que un humano lo revise (human-in-the-loop, fase 8.5).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

from harness.executor import turn_trace
from harness.validation import Validator, run_validators
from headache_core.application.agents.runtime import AgentRuntime, AgentTurn
from headache_core.application.tools import FALLBACK_ANSWER, ToolContext, ToolExecution

logger = logging.getLogger(__name__)

BLOCKED_INPUT_ANSWER = (
    "No puedo ayudarte con esa petición. Si necesitas algo sobre tus eventos o pedidos de "
    "Headache, cuéntamelo de otra forma."
)


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """Lo que ven las guardas de salida: la pregunta, la respuesta y las tools del turno."""

    message: str
    answer: str
    executions: tuple[ToolExecution, ...]


class GuardedRuntime:
    def __init__(
        self,
        inner: AgentRuntime,
        *,
        input_guards: Sequence[Validator[str, ToolContext]] = (),
        output_guards: Sequence[Validator[TurnRecord, ToolContext]] = (),
        blocked_input_answer: str = BLOCKED_INPUT_ANSWER,
        blocked_output_answer: str = FALLBACK_ANSWER,
    ) -> None:
        self._inner = inner
        self._input_guards = tuple(input_guards)
        self._output_guards = tuple(output_guards)
        self._blocked_input = blocked_input_answer
        self._blocked_output = blocked_output_answer

    @property
    def flavor(self) -> str:
        return self._inner.flavor

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        violations = await run_validators(self._input_guards, message, context)
        if violations:
            codes = tuple(v.code for v in violations)
            logger.warning("Entrada bloqueada por el harness: %s", codes)
            return AgentTurn(self._blocked_input, "guardrail", guardrails=codes)
        with turn_trace() as trace:
            turn = await self._inner.respond(thread_id, message, context)
        record = TurnRecord(message, turn.answer, tuple(trace.executions))
        violations = await run_validators(self._output_guards, record, context)
        if violations:
            codes = tuple(v.code for v in violations)
            logger.warning("Respuesta bloqueada por el harness: %s", codes)
            return replace(
                turn, answer=self._blocked_output, stop_reason="guardrail", guardrails=codes
            )
        return turn

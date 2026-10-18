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
from harness.pii import TextFilter
from harness.validation import Validator, run_validators
from headache_core.application.agents.runtime import AgentRuntime, AgentTurn
from headache_core.application.tools import FALLBACK_ANSWER, ToolContext, ToolExecution
from headache_core.ports.llm import LLMContentFilterError

logger = logging.getLogger(__name__)
PROVIDER_FILTER = "provider_content_filter"

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
        input_filters: Sequence[TextFilter] = (),
        output_filters: Sequence[TextFilter] = (),
        blocked_input_answer: str = BLOCKED_INPUT_ANSWER,
        blocked_output_answer: str = FALLBACK_ANSWER,
    ) -> None:
        self._inner = inner
        self._input_guards = tuple(input_guards)
        self._output_guards = tuple(output_guards)
        self._input_filters = tuple(input_filters)
        self._output_filters = tuple(output_filters)
        self._blocked_input = blocked_input_answer
        self._blocked_output = blocked_output_answer

    @property
    def flavor(self) -> str:
        return self._inner.flavor

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        # Las guardas ven el texto original; el agente (y su hilo) solo el filtrado.
        violations = await run_validators(self._input_guards, message, context)
        if violations:
            codes = tuple(v.code for v in violations)
            logger.warning("Entrada bloqueada por el harness: %s", codes)
            return AgentTurn(self._blocked_input, "guardrail", guardrails=codes)
        message, input_codes = await _apply(self._input_filters, message, context)
        with turn_trace() as trace:
            try:
                turn = await self._inner.respond(thread_id, message, context)
            except LLMContentFilterError:
                # El filtro del proveedor (p. ej., Prompt Shields de Azure) es otra capa de
                # defensa: si bloquea lo que se nos escapó, se responde igual que un bloqueo
                # propio en lugar de tumbar el turno con un 500 (medido en Azure).
                logger.warning("Petición bloqueada por el filtro de contenido del proveedor")
                codes = (*input_codes, PROVIDER_FILTER)
                return AgentTurn(self._blocked_input, "guardrail", guardrails=codes)
        record = TurnRecord(message, turn.answer, tuple(trace.executions))
        violations = await run_validators(self._output_guards, record, context)
        if violations:
            codes = tuple(
                dict.fromkeys((*input_codes, *trace.findings, *(v.code for v in violations)))
            )
            logger.warning("Respuesta bloqueada por el harness: %s", codes)
            return replace(
                turn, answer=self._blocked_output, stop_reason="guardrail", guardrails=codes
            )
        answer, output_codes = await _apply(self._output_filters, turn.answer, context)
        codes = tuple(dict.fromkeys((*input_codes, *trace.findings, *output_codes)))
        return replace(turn, answer=answer, guardrails=codes)


async def _apply(
    filters: Sequence[TextFilter], text: str, context: ToolContext
) -> tuple[str, tuple[str, ...]]:
    codes: list[str] = []
    for text_filter in filters:
        text, found = await text_filter(text, context)
        codes += found
    return text, tuple(codes)

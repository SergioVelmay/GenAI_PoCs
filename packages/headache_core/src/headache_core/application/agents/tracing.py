"""Un span `invoke_agent {nombre}` por turno, envolviendo cualquier `AgentRuntime` (fase 11.2).

Va por fuera del harness: el span cubre guardas, modelo y tools, y deja como atributos lo que
decidió el turno (motivo de parada, reglas disparadas, aprobaciones abiertas). Dentro cuelgan
los spans `chat` del gateway y `execute_tool` del ejecutor, use el framework que use.
"""

from __future__ import annotations

from headache_core.application.agents.runtime import AgentRuntime, AgentTurn
from headache_core.application.telemetry import genai, get_telemetry
from headache_core.application.tools.registry import ToolContext
from headache_core.ports.telemetry import AttributeValue


class TracedAgent:
    def __init__(self, inner: AgentRuntime, *, name: str) -> None:
        self._inner = inner
        self._name = name

    @property
    def flavor(self) -> str:
        return self._inner.flavor

    @property
    def inner(self) -> AgentRuntime:
        return self._inner

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        telemetry = get_telemetry()
        attributes: dict[str, AttributeValue] = {
            genai.OPERATION_NAME: genai.GenAIOperation.INVOKE_AGENT.value,
            genai.LANGFUSE_OBSERVATION_TYPE: genai.ObservationType.AGENT.value,
            genai.AGENT_NAME: self._name,
            genai.HEADACHE_AGENT_FLAVOR: self._inner.flavor,
            # El hilo agrupa los turnos de una conversación (una sesión en Langfuse).
            genai.CONVERSATION_ID: thread_id,
            genai.LANGFUSE_SESSION_ID: thread_id,
        }
        prompt = telemetry.content(message)
        if prompt is not None:
            attributes[genai.LANGFUSE_INPUT] = prompt
        with telemetry.span(
            genai.span_name(genai.GenAIOperation.INVOKE_AGENT, self._name),
            attributes=attributes,
        ) as span:
            turn = await self._inner.respond(thread_id, message, context)
            span.set_attributes(
                {
                    genai.HEADACHE_STOP_REASON: turn.stop_reason,
                    genai.HEADACHE_GUARDRAILS: list(turn.guardrails),
                    genai.HEADACHE_APPROVALS: len(turn.approvals),
                    genai.HEADACHE_COMPACTED: turn.compacted,
                }
            )
            answer = telemetry.content(turn.answer)
            if answer is not None:
                span.set_attribute(genai.LANGFUSE_OUTPUT, answer)
            return turn

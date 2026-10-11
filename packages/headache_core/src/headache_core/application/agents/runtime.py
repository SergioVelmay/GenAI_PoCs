"""Contrato común de cualquier agente: un turno de un hilo. Lo implementan los sabores (fase 7) y
lo envuelve el harness (fase 8) sin saber qué framework hay debajo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from headache_core.application.tools.registry import ToolContext

# "guardrail": el harness bloqueó la respuesta del agente y la sustituyó por una segura.
type StopReason = Literal["answer", "max_iterations", "guardrail"]


@dataclass(frozen=True, slots=True)
class AgentTurn:
    answer: str
    stop_reason: StopReason
    # ¿Se compactó la memoria a corto plazo antes de este turno?
    compacted: bool = False
    # Códigos de las reglas del harness que se dispararon en el turno.
    guardrails: tuple[str, ...] = ()


class AgentRuntime(Protocol):
    @property
    def flavor(self) -> str: ...

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn: ...

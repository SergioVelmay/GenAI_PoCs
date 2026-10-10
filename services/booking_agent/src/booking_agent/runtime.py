"""Contrato común de los cinco sabores y piezas que comparten.

- `AgentRuntime`: la única interfaz que ve la API (y la suite de evals): un turno de un hilo.
- `JournaledExecutor`: TODAS las tools pasan por el mismo ejecutor, use el framework que use.
  Mismas garantías (validación, timeouts, errores tipados, saneamiento, escrituras en serie) y
  un diario de ejecuciones que la eval lee sin fiarse de lo que cuente cada framework.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal, Protocol

from booking_agent.spec import AgentSpec
from headache_core.application.tools import ToolContext, ToolExecution, ToolExecutor, ToolRegistry
from headache_core.ports import LLMPort, ToolCall
from headache_core.ports.memory import CheckpointStore, PreferenceStore

type StopReason = Literal["answer", "max_iterations"]


@dataclass(frozen=True, slots=True)
class AgentTurn:
    answer: str
    stop_reason: StopReason
    # ¿Se compactó la memoria a corto plazo antes de este turno?
    compacted: bool = False


class AgentRuntime(Protocol):
    @property
    def flavor(self) -> str: ...

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn: ...


class JournaledExecutor(ToolExecutor):
    def __init__(self, registry: ToolRegistry, *, max_output_chars: int = 4000) -> None:
        super().__init__(registry, max_output_chars=max_output_chars)
        self._registry = registry
        # Un solo lock de escritura: aunque el framework ejecute tools en paralelo, los efectos
        # secundarios (reembolsos, memoria) nunca compiten entre sí.
        self._write_lock = asyncio.Lock()
        self.journal: list[ToolExecution] = []

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolExecution:
        tool = self._registry.get(call.name)
        if tool is not None and not tool.read_only:
            async with self._write_lock:
                execution = await super().execute(call, context)
        else:
            execution = await super().execute(call, context)
        self.journal.append(execution)
        return execution


@dataclass(frozen=True, slots=True)
class AgentDeps:
    spec: AgentSpec
    llm: LLMPort
    executor: JournaledExecutor
    checkpoints: CheckpointStore
    preferences: PreferenceStore
    # Modelo del resumidor: por defecto el mismo gateway (el router elige el modelo barato).
    summarizer: LLMPort | None = None

"""Contrato común de los cinco sabores y piezas que comparten.

- `AgentRuntime` (en `headache_core`): la única interfaz que ve la API, la suite de evals y el
  harness (fase 8): un turno de un hilo.
- `JournaledExecutor`: TODAS las tools pasan por el mismo ejecutor, use el framework que use.
  Mismas garantías (validación, timeouts, errores tipados, saneamiento, escrituras en serie) y
  un diario de ejecuciones que la eval lee sin fiarse de lo que cuente cada framework.
  Con `inner` delega en otro ejecutor (p. ej. `harness.GuardedExecutor`) sin tocar los sabores.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from booking_agent.spec import AgentSpec
from headache_core.application.agents.runtime import AgentRuntime, AgentTurn, StopReason
from headache_core.application.tools import ToolContext, ToolExecution, ToolExecutor, ToolRegistry
from headache_core.ports import LLMPort, ToolCall
from headache_core.ports.memory import CheckpointStore, PreferenceStore

__all__ = ["AgentDeps", "AgentRuntime", "AgentTurn", "JournaledExecutor", "StopReason"]


class JournaledExecutor(ToolExecutor):
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        max_output_chars: int = 4000,
        inner: ToolExecutor | None = None,
    ) -> None:
        super().__init__(registry, max_output_chars=max_output_chars)
        self._registry = registry
        self._inner = inner
        # Un solo lock de escritura: aunque el framework ejecute tools en paralelo, los efectos
        # secundarios (reembolsos, memoria) nunca compiten entre sí.
        self._write_lock = asyncio.Lock()
        self.journal: list[ToolExecution] = []

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolExecution:
        tool = self._registry.get(call.name)
        if tool is not None and not tool.read_only:
            async with self._write_lock:
                execution = await self._run(call, context)
        else:
            execution = await self._run(call, context)
        self.journal.append(execution)
        return execution

    async def _run(self, call: ToolCall, context: ToolContext) -> ToolExecution:
        if self._inner is not None:
            return await self._inner.execute(call, context)
        return await super().execute(call, context)


@dataclass(frozen=True, slots=True)
class AgentDeps:
    spec: AgentSpec
    llm: LLMPort
    executor: JournaledExecutor
    checkpoints: CheckpointStore
    preferences: PreferenceStore
    # Modelo del resumidor: por defecto el mismo gateway (el router elige el modelo barato).
    summarizer: LLMPort | None = None

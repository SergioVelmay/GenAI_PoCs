"""Agente mínimo (carril B): prompt de sistema + bucle propio + tools descubiertas por MCP."""

from __future__ import annotations

from headache_core.application.tools import (
    TICKETING_AGENT_PROMPT,
    ToolContext,
    ToolLoop,
    ToolLoopResult,
    ToolRegistry,
)
from headache_core.ports import ChatMessage, LLMPort, Role


class SupportAgent:
    def __init__(self, llm: LLMPort, tools: ToolRegistry, *, max_iterations: int = 5) -> None:
        self._loop = ToolLoop(llm, tools, max_iterations=max_iterations)

    async def answer(self, question: str, context: ToolContext) -> ToolLoopResult:
        return await self._loop.run(
            [
                # Prefijo estable (prompt + tools): candidato a prompt caching.
                ChatMessage(Role.SYSTEM, TICKETING_AGENT_PROMPT, cache_checkpoint=True),
                ChatMessage(Role.USER, question),
            ],
            context,
        )

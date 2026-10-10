"""Sabor carril B: bucle propio (`ToolLoop`) + context engineering propio. La referencia.

Checkpoint al final de cada turno (no tras cada paso, como LangGraph): reanudar a mitad de un
turno o pausar para aprobación humana (fase 8) exigirá guardar también tras cada paso.
"""

from __future__ import annotations

from dataclasses import replace

from booking_agent.runtime import AgentDeps, AgentTurn
from headache_core.application.agents import ConversationSummarizer, ShortTermMemory, build_prompt
from headache_core.application.agents.context import load_thread
from headache_core.application.tools import ToolContext, ToolLoop
from headache_core.ports import ChatMessage, Role
from headache_core.ports.memory import ThreadState


class NativeAgent:
    flavor = "native"

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps
        policy = deps.spec.context
        summarizer = ConversationSummarizer(deps.summarizer or deps.llm, policy)
        self._memory = ShortTermMemory(policy, summarizer if policy.summarize else None)
        self._loop = ToolLoop(
            deps.llm,
            deps.spec.tools,
            max_iterations=deps.spec.max_iterations,
            fallback_answer=deps.spec.fallback_answer,
            executor=deps.executor,
        )

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        deps = self._deps
        loaded = await load_thread(deps.checkpoints, thread_id, context.customer_id)
        state, compacted = await self._memory.compact(loaded)
        prompt = build_prompt(
            deps.spec.system_prompt,
            preferences=await deps.preferences.get(context.customer_id),
            summary=state.summary,
            history=state.messages,
            user_message=message,
        )
        result = await self._loop.run(prompt, context)
        # Se guarda desde el mensaje del cliente: el prefijo se reconstruye en cada turno.
        turn = [replace(m, cache_checkpoint=False) for m in result.messages[len(prompt) - 1 :]]
        if result.stop_reason == "max_iterations":
            # El historial debe cerrar el intercambio con lo que el cliente sí vio.
            turn.append(ChatMessage(Role.ASSISTANT, result.answer))
        await deps.checkpoints.save(
            ThreadState(
                thread_id=thread_id,
                customer_id=context.customer_id,
                messages=(*state.messages, *turn),
                summary=state.summary,
            ),
            expected_version=loaded.version,
        )
        return AgentTurn(result.answer, result.stop_reason, compacted)


def create(deps: AgentDeps) -> NativeAgent:
    return NativeAgent(deps)

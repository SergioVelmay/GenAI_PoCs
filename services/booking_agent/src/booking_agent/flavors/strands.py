"""Sabor carril C: Strands Agents (AWS; el SDK sobre el que corre AgentCore Runtime).

Agente "model-driven": un bucle mínimo (modelo → tools → modelo) configurado por piezas.
- Límite: `limits={"turns": N}` tiene la misma semántica que el bucle native (N llamadas al
  modelo, se ejecutan las tools de cada una) y termina con `stop_reason="limit_turns"`.
- Identidad: `invocation_state`, que llega a las tools y nunca al modelo.
- Caché: bloques `cachePoint` nativos de Converse en el system prompt y al final del historial.
- Memoria: `NullConversationManager` (la compactación es nuestra política, como en todos los
  sabores). Las alternativas nativas son `SlidingWindowConversationManager` y
  `SummarizingConversationManager`, y el `session_manager` (S3/fichero o AgentCore Memory)
  para persistir; aquí el hilo vive en nuestro `CheckpointStore`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from strands import Agent
from strands.agent.conversation_manager import NullConversationManager
from strands.types.tools import AgentTool, ToolGenerator, ToolUse
from strands.types.tools import ToolSpec as StrandsToolSpec

from booking_agent.bridges.strands import (
    GatewayModel,
    arguments_of,
    message_text,
    system_blocks,
    to_chat_messages,
    to_strands_messages,
    tool_spec,
)
from booking_agent.runtime import AgentDeps, AgentTurn, JournaledExecutor, StopReason
from headache_core.application.agents import (
    ConversationSummarizer,
    ShortTermMemory,
    build_prompt_prefix,
)
from headache_core.application.agents.context import load_thread
from headache_core.application.tools import Tool, ToolContext
from headache_core.ports import ChatMessage, Role, ToolCall
from headache_core.ports.memory import ThreadState

TOOL_CONTEXT = "tool_context"


class JournaledTool(AgentTool):
    """Tool de Strands que delega en el ejecutor común (validación, timeouts, diario)."""

    def __init__(self, tool: Tool[Any], executor: JournaledExecutor) -> None:
        super().__init__()
        self._spec = tool_spec(tool.spec())
        self._executor = executor

    @property
    def tool_name(self) -> str:
        return self._spec["name"]

    @property
    def tool_spec(self) -> StrandsToolSpec:
        return self._spec

    @property
    def tool_type(self) -> str:
        return "headache"

    async def stream(
        self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any
    ) -> ToolGenerator:
        context: ToolContext = invocation_state[TOOL_CONTEXT]
        call = ToolCall(tool_use["toolUseId"], self.tool_name, arguments_of(tool_use["input"]))
        execution = await self._executor.execute(call, context)
        yield {
            "toolUseId": tool_use["toolUseId"],
            "status": "error" if execution.is_error else "success",
            "content": [{"text": execution.output}],
        }


class StrandsAgent:
    flavor = "strands"

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps
        policy = deps.spec.context
        summarizer = ConversationSummarizer(deps.summarizer or deps.llm, policy)
        self._memory = ShortTermMemory(policy, summarizer if policy.summarize else None)
        self._model = GatewayModel(deps.llm, [tool.spec() for tool in deps.spec.tools])
        self._tools = [JournaledTool(tool, deps.executor) for tool in deps.spec.tools]

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        deps = self._deps
        loaded = await load_thread(deps.checkpoints, thread_id, context.customer_id)
        state, compacted = await self._memory.compact(loaded)
        prefix = build_prompt_prefix(
            deps.spec.system_prompt,
            preferences=await deps.preferences.get(context.customer_id),
            summary=state.summary,
        )
        history = [replace(m, cache_checkpoint=False) for m in state.messages]
        if history:
            history[-1] = replace(history[-1], cache_checkpoint=True)
        seed = to_strands_messages(history)
        # Strands adopta la lista y la amplía en sitio: se fija dónde empieza el turno.
        turn_start = len(seed)
        agent = Agent(
            model=self._model,
            messages=seed,
            tools=list(self._tools),
            system_prompt=system_blocks(prefix),
            name=deps.spec.name,
            callback_handler=None,
            conversation_manager=NullConversationManager(),
        )
        result = await agent.invoke_async(
            message,
            invocation_state={TOOL_CONTEXT: context},
            limits={"turns": deps.spec.max_iterations},
        )
        turn = [
            replace(m, cache_checkpoint=False)
            for m in to_chat_messages(agent.messages[turn_start:])
        ]
        stop_reason: StopReason = "answer"
        answer = message_text(result.message)
        if result.stop_reason == "limit_turns":
            stop_reason, answer = "max_iterations", deps.spec.fallback_answer
            turn.append(ChatMessage(Role.ASSISTANT, answer))
        await deps.checkpoints.save(
            ThreadState(
                thread_id=thread_id,
                customer_id=context.customer_id,
                messages=(*state.messages, *turn),
                summary=state.summary,
            ),
            expected_version=loaded.version,
        )
        return AgentTurn(answer, stop_reason, compacted)


def create(deps: AgentDeps) -> StrandsAgent:
    return StrandsAgent(deps)

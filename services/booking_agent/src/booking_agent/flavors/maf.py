"""Sabor carril C: Microsoft Agent Framework (sucesor de Semantic Kernel y AutoGen).

Cada pieza del context engineering usa el punto de extensión nativo de MAF:
- `ThreadHistory` (`HistoryProvider`): carga el historial ya compactado y, al acabar el turno,
  lo persiste en nuestro `CheckpointStore` (dueño + concurrencia optimista).
- `SessionContextProvider` (`ContextProvider`): inyecta preferencias y resumen detrás de las
  instrucciones (que son el prefijo estable de la caché).
- `FallbackOnMaxIterations` (`ChatMiddleware`): al agotar `max_iterations`, MAF pide al modelo una
  respuesta final sin tools; el middleware la sustituye por el fallback determinista sin
  llamar al modelo (mismas llamadas y misma respuesta que el sabor native).
- Identidad: `function_invocation_kwargs` → `FunctionInvocationContext.kwargs`, fuera del
  alcance del modelo. Las tools se registran con un esquema permisivo para que MAF no valide
  por su cuenta: valida nuestro ejecutor (el puente envía al modelo el `ToolSpec` real).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any
from uuid import uuid4

from agent_framework import (
    Agent,
    AgentSession,
    ChatContext,
    ChatMiddleware,
    ChatResponse,
    ContextProvider,
    FunctionInvocationContext,
    FunctionTool,
    HistoryProvider,
    Message,
    SessionContext,
    SupportsAgentRun,
)

from booking_agent.bridges.maf import (
    RAW_ARGUMENTS,
    GatewayChatClient,
    to_chat_messages,
    to_maf_messages,
)
from booking_agent.runtime import AgentDeps, AgentTurn, StopReason
from headache_core.application.agents import (
    ConversationSummarizer,
    ShortTermMemory,
    render_session_context,
)
from headache_core.application.agents.context import load_thread
from headache_core.application.tools import Tool, ToolContext
from headache_core.ports import ToolCall
from headache_core.ports.memory import CheckpointStore, ThreadState

TOOL_CONTEXT = "tool_context"
STOP_REASON = "stop_reason"
# Argumentos libres: la validación es de nuestro ejecutor, no de MAF.
_OPEN_SCHEMA = {"type": "object", "additionalProperties": True}


class FallbackOnMaxIterations(ChatMiddleware):
    def __init__(self, fallback_answer: str) -> None:
        self._fallback = fallback_answer

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        if (context.options or {}).get("tool_choice") == "none":
            reply = Message(
                "assistant", [self._fallback], additional_properties={STOP_REASON: "max_iterations"}
            )
            context.result = ChatResponse(messages=[reply])
            return
        await call_next()


class SessionContextProvider(ContextProvider):
    """Preferencias + resumen: el segundo bloque del prompt, detrás de las instrucciones."""

    def __init__(self, rendered: str) -> None:
        super().__init__("session_context")
        self._rendered = rendered

    async def before_run(
        self,
        *,
        agent: SupportsAgentRun,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        if self._rendered:
            context.extend_messages(self, [Message("system", [self._rendered])])


class ThreadHistory(HistoryProvider):
    def __init__(self, store: CheckpointStore, loaded: ThreadState, compacted: ThreadState) -> None:
        super().__init__("thread")
        self._store = store
        self._loaded = loaded
        self._state = compacted

    async def get_messages(
        self, session_id: str | None, *, state: dict[str, Any] | None = None, **kwargs: Any
    ) -> list[Message]:
        history = list(self._state.messages)
        if history:
            history[-1] = replace(history[-1], cache_checkpoint=True)
        return to_maf_messages(history)

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        turn = [replace(m, cache_checkpoint=False) for m in to_chat_messages(messages)]
        await self._store.save(
            replace(self._state, messages=(*self._state.messages, *turn)),
            expected_version=self._loaded.version,
        )


class MafAgent:
    flavor = "maf"

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps
        policy = deps.spec.context
        summarizer = ConversationSummarizer(deps.summarizer or deps.llm, policy)
        self._memory = ShortTermMemory(policy, summarizer if policy.summarize else None)
        self._client = GatewayChatClient(
            deps.llm,
            [tool.spec() for tool in deps.spec.tools],
            function_invocation_configuration={"max_iterations": deps.spec.max_iterations},
            middleware=[FallbackOnMaxIterations(deps.spec.fallback_answer)],
        )
        self._tools = [self._tool(tool) for tool in deps.spec.tools]

    def _tool(self, tool: Tool[Any]) -> FunctionTool:
        executor = self._deps.executor
        name = tool.name

        async def call(ctx: FunctionInvocationContext, **kwargs: Any) -> str:
            context: ToolContext = ctx.kwargs[TOOL_CONTEXT]
            raw = kwargs.pop(RAW_ARGUMENTS, None)
            arguments = raw if isinstance(raw, str) else json.dumps(kwargs, ensure_ascii=False)
            # MAF no expone el id de la llamada a la función: el diario usa uno propio.
            execution = await executor.execute(
                ToolCall(f"maf_{uuid4().hex[:12]}", name, arguments), context
            )
            return execution.output

        return FunctionTool(
            name=name, description=tool.description, func=call, input_model=_OPEN_SCHEMA
        )

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        deps = self._deps
        loaded = await load_thread(deps.checkpoints, thread_id, context.customer_id)
        state, compacted = await self._memory.compact(loaded)
        rendered = render_session_context(
            await deps.preferences.get(context.customer_id), state.summary
        )
        agent = Agent(
            self._client,
            instructions=deps.spec.system_prompt,
            name=deps.spec.name,
            tools=self._tools,
            context_providers=[
                SessionContextProvider(rendered),
                ThreadHistory(deps.checkpoints, loaded, state),
            ],
        )
        response = await agent.run(
            message,
            session=AgentSession(session_id=thread_id),
            function_invocation_kwargs={TOOL_CONTEXT: context},
        )
        stop_reason: StopReason = (
            "max_iterations"
            if any(
                (m.additional_properties or {}).get(STOP_REASON) == "max_iterations"
                for m in response.messages
            )
            else "answer"
        )
        return AgentTurn(response.text, stop_reason, compacted)


def create(deps: AgentDeps) -> MafAgent:
    return MafAgent(deps)

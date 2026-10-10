"""Sabor carril C: LangGraph con un `StateGraph` explícito.

    START → prepare → agent ⇄ tools → (fallback) → END

- Estado: `messages` con el reducer `add_messages` (añade, sustituye por id o borra con
  `RemoveMessage`), más `summary`, la instantánea de preferencias del turno y contadores.
- Persistencia: el checkpointer de LangGraph guarda el estado tras CADA paso (super-step) por
  `thread_id`. Es lo que permite reanudar o pausar para aprobación humana (fase 8). En memoria
  aquí; en producción `PostgresSaver` (Aurora/Azure PostgreSQL) o el de LangGraph Platform.
- Identidad: viaja en el contexto de runtime (`context_schema=ToolContext`), que no se persiste
  ni lo ve el modelo. Las tools se ejecutan en un nodo propio sobre `JournaledExecutor`.
- Memoria a largo plazo: nuestro `PreferenceStore` (el equivalente nativo es el `BaseStore` de
  LangGraph, por namespaces). Compactación: nuestra política, aplicada en el nodo `prepare`.

Alternativa de alto nivel: `langchain.agents.create_agent` + middleware (p. ej.
`SummarizationMiddleware`) compila un grafo parecido; aquí se construye a mano para ver cada pieza.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal, TypedDict, cast

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from booking_agent.bridges.langchain import (
    GatewayChatModel,
    spec_to_openai,
    system_message,
    to_chat_messages,
    tool_calls_of,
    with_cache_checkpoint,
)
from booking_agent.runtime import AgentDeps, AgentTurn, StopReason
from headache_core.application.agents import (
    ConversationSummarizer,
    ShortTermMemory,
    build_prompt_prefix,
)
from headache_core.application.tools import ToolContext
from headache_core.ports.memory import ThreadAccessError, ThreadState


class BookingState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    customer_id: str
    summary: str
    # Instantánea por turno: el prefijo no cambia a mitad de turno (caché y mismo comportamiento
    # que el sabor native, que ve las preferencias nuevas en el turno siguiente).
    preferences: dict[str, str]
    turn_start: int
    iterations: int
    compacted: bool
    stop_reason: StopReason


type Update = dict[str, Any]


class LangGraphAgent:
    flavor = "langgraph"

    def __init__(self, deps: AgentDeps, checkpointer: BaseCheckpointSaver[Any] | None = None):
        self._deps = deps
        policy = deps.spec.context
        summarizer = ConversationSummarizer(deps.summarizer or deps.llm, policy)
        self._memory = ShortTermMemory(policy, summarizer if policy.summarize else None)
        self._model = GatewayChatModel(gateway=deps.llm).bind_tools(
            [spec_to_openai(tool.spec()) for tool in deps.spec.tools]
        )
        self.graph = self._build(checkpointer or InMemorySaver())

    def _build(
        self, checkpointer: BaseCheckpointSaver[Any]
    ) -> CompiledStateGraph[BookingState, ToolContext, BookingState, BookingState]:
        graph = StateGraph(BookingState, context_schema=ToolContext)
        graph.add_node("prepare", self._prepare)
        graph.add_node("agent", self._agent)
        graph.add_node("tools", self._tools)
        graph.add_node("fallback", self._fallback)
        graph.add_edge(START, "prepare")
        graph.add_edge("prepare", "agent")
        graph.add_conditional_edges("agent", self._after_agent, ["tools", END])
        graph.add_conditional_edges("tools", self._after_tools, ["agent", "fallback"])
        graph.add_edge("fallback", END)
        return graph.compile(checkpointer=checkpointer, name=self._deps.spec.name)

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        config: RunnableConfig = {
            "configurable": {"thread_id": thread_id},
            # prepare + (agent, tools) x N + fallback: el límite propio llega antes.
            "recursion_limit": 2 * self._deps.spec.max_iterations + 4,
        }
        # El dueño se comprueba ANTES de invocar: la entrada se persiste en el primer paso,
        # así que validarlo dentro del grafo dejaría escribir en hilos ajenos.
        snapshot = await self.graph.aget_state(config)
        owner = snapshot.values.get("customer_id")
        if owner is not None and owner != context.customer_id:
            raise ThreadAccessError(f"Hilo {thread_id} no encontrado.")
        turn_input: BookingState = {
            "messages": [HumanMessage(message)],
            "customer_id": context.customer_id,
            "iterations": 0,
            "compacted": False,
            "stop_reason": "answer",
        }
        result = await self.graph.ainvoke(turn_input, config, context=context)
        last = cast(BaseMessage, result["messages"][-1])
        return AgentTurn(last.text, result["stop_reason"], result["compacted"])

    async def _prepare(self, state: BookingState, runtime: Runtime[ToolContext]) -> Update:
        messages = state["messages"]
        history = messages[:-1]
        thread = ThreadState(
            thread_id="",
            customer_id=runtime.context.customer_id,
            messages=tuple(to_chat_messages(history)),
            summary=state.get("summary", ""),
        )
        compacted, did_compact = await self._memory.compact(thread)
        update: Update = {
            "preferences": dict(await self._deps.preferences.get(runtime.context.customer_id)),
            "turn_start": len(messages) - 1,
        }
        if did_compact:
            evicted = history[: len(history) - len(compacted.messages)]
            update |= {
                "messages": [RemoveMessage(id=m.id) for m in evicted if m.id is not None],
                "summary": compacted.summary,
                "turn_start": len(messages) - 1 - len(evicted),
                "compacted": True,
            }
        return update

    async def _agent(self, state: BookingState) -> Update:
        prefix = build_prompt_prefix(
            self._deps.spec.system_prompt,
            preferences=state.get("preferences", {}),
            summary=state.get("summary", ""),
        )
        conversation: list[BaseMessage] = list(state["messages"])
        start = state.get("turn_start", 0)
        if start > 0:
            # Checkpoint de caché al final del historial previo (no se guarda en el estado).
            conversation[start - 1] = with_cache_checkpoint(conversation[start - 1])
        reply = await self._model.ainvoke([*map(system_message, prefix), *conversation])
        return {"messages": [reply], "iterations": state.get("iterations", 0) + 1}

    async def _tools(self, state: BookingState, runtime: Runtime[ToolContext]) -> Update:
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        # Mismas garantías que el bucle native: el lock del ejecutor serializa las escrituras.
        executions = await asyncio.gather(
            *(self._deps.executor.execute(call, runtime.context) for call in tool_calls_of(last))
        )
        return {
            "messages": [
                ToolMessage(
                    e.output, tool_call_id=e.call.id, status="error" if e.is_error else "success"
                )
                for e in executions
            ]
        }

    async def _fallback(self, state: BookingState) -> Update:
        return {
            "messages": [AIMessage(self._deps.spec.fallback_answer)],
            "stop_reason": "max_iterations",
        }

    @staticmethod
    def _after_agent(state: BookingState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and (last.tool_calls or last.invalid_tool_calls):
            return "tools"
        return END

    def _after_tools(self, state: BookingState) -> Literal["agent", "fallback"]:
        if state.get("iterations", 0) >= self._deps.spec.max_iterations:
            return "fallback"
        return "agent"


def create(deps: AgentDeps) -> LangGraphAgent:
    return LangGraphAgent(deps)

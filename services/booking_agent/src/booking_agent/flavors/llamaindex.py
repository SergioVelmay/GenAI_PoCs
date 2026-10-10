"""Sabor carril C: LlamaIndex `FunctionAgent` (Agent Workflows sobre `llama-index-workflows`).

- El bucle lo dirige el workflow del agente (eventos AgentInput → AgentOutput → ToolCall...);
  el LLM es el puente `GatewayFunctionLLM` y cada tool es un `FunctionTool` que delega en
  `JournaledExecutor` con la identidad atada por closure (el modelo nunca la ve).
- Memoria del turno: un `BaseMemory` de lista (`TurnMemory`) sembrado con prefijo + historial
  compactado. NO se usa `Memory` de LlamaIndex: su almacén SQL ordena por `time.time_ns()` y
  con la resolución del reloj de Windows los mensajes del turno pueden quedar antes que la
  semilla (el modelo ve la conversación desordenada). El hilo se persiste en nuestro
  `CheckpointStore` (dueño + concurrencia optimista), igual que el sabor native. Alternativas
  nativas: `Memory` con base de datos SQL por `session_id` y bloques de memoria a largo plazo
  (`StaticMemoryBlock`, `FactExtractionMemoryBlock`...) o serializar el `Context` del workflow.
- `max_iterations` de LlamaIndex cuenta respuestas del modelo y lanza `WorkflowRuntimeError`
  ANTES de ejecutar las tools de la última: para ejecutar las mismas tools que el bucle native
  se pasa N + 1, a costa de una llamada al modelo más en ese caso.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

from llama_index.core.agent.workflow import AgentOutput, FunctionAgent
from llama_index.core.base.llms.types import ChatMessage as LIChatMessage
from llama_index.core.memory.types import BaseMemory
from llama_index.core.tools import FunctionTool
from pydantic import BaseModel, ConfigDict, Field
from workflows.errors import WorkflowRuntimeError

from booking_agent.bridges.llamaindex import (
    RAW_ARGUMENTS,
    GatewayFunctionLLM,
    to_chat_messages,
    to_llama_messages,
)
from booking_agent.runtime import AgentDeps, AgentTurn, StopReason
from headache_core.application.agents import (
    ConversationSummarizer,
    ShortTermMemory,
    build_prompt_prefix,
)
from headache_core.application.agents.context import load_thread
from headache_core.application.tools import ToolContext
from headache_core.ports import ChatMessage, Role, ToolCall
from headache_core.ports.memory import ThreadState


class TurnMemory(BaseMemory):
    """Memoria de un turno: lista en orden de inserción, sin recorte (lo decide la política)."""

    messages: list[LIChatMessage] = Field(default_factory=list)

    @classmethod
    def class_name(cls) -> str:
        return "TurnMemory"

    @classmethod
    def from_defaults(cls, **kwargs: Any) -> TurnMemory:
        return cls(**kwargs)

    def get(self, input: str | None = None, **kwargs: Any) -> list[LIChatMessage]:
        return list(self.messages)

    def get_all(self) -> list[LIChatMessage]:
        return list(self.messages)

    def put(self, message: LIChatMessage) -> None:
        self.messages.append(message)

    def set(self, messages: list[LIChatMessage]) -> None:
        self.messages = list(messages)

    def reset(self) -> None:
        self.messages = []

    async def aget(self, input: str | None = None, **kwargs: Any) -> list[LIChatMessage]:
        return self.get(input, **kwargs)

    async def aget_all(self) -> list[LIChatMessage]:
        return self.get_all()

    async def aput(self, message: LIChatMessage) -> None:
        self.put(message)

    async def aput_messages(self, messages: list[LIChatMessage]) -> None:
        self.messages.extend(messages)

    async def aset(self, messages: list[LIChatMessage]) -> None:
        self.set(messages)


class _OpenArgs(BaseModel):
    """Esquema de relleno: el puente envía al modelo el `ToolSpec` real de cada tool."""

    model_config = ConfigDict(extra="allow")


class LlamaIndexAgent:
    flavor = "llamaindex"

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps
        policy = deps.spec.context
        summarizer = ConversationSummarizer(deps.summarizer or deps.llm, policy)
        self._memory = ShortTermMemory(policy, summarizer if policy.summarize else None)
        self._llm = GatewayFunctionLLM(deps.llm, [tool.spec() for tool in deps.spec.tools])

    def _tool(self, name: str, description: str, context: ToolContext) -> FunctionTool:
        executor = self._deps.executor

        async def call(**kwargs: Any) -> str:
            raw = kwargs.pop(RAW_ARGUMENTS, None)
            arguments = raw if isinstance(raw, str) else json.dumps(kwargs, ensure_ascii=False)
            # LlamaIndex no pasa el id de la llamada a la función: el diario usa uno propio.
            execution = await executor.execute(
                ToolCall(f"li_{uuid4().hex[:12]}", name, arguments), context
            )
            return execution.output

        return FunctionTool.from_defaults(
            async_fn=call, name=name, description=description, fn_schema=_OpenArgs
        )

    def _agent(self, context: ToolContext) -> FunctionAgent:
        spec = self._deps.spec
        return FunctionAgent(
            name=spec.name,
            description="Knowledge & Booking Agent de Headache",
            # Sin system_prompt: el prefijo (con sus checkpoints de caché) va en chat_history.
            tools=[self._tool(t.name, t.description, context) for t in spec.tools],
            llm=self._llm,
            streaming=False,
        )

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        deps = self._deps
        loaded = await load_thread(deps.checkpoints, thread_id, context.customer_id)
        state, compacted = await self._memory.compact(loaded)
        history = [replace(m, cache_checkpoint=False) for m in state.messages]
        if history:
            history[-1] = replace(history[-1], cache_checkpoint=True)
        prefix = build_prompt_prefix(
            deps.spec.system_prompt,
            preferences=await deps.preferences.get(context.customer_id),
            summary=state.summary,
        )
        seed = to_llama_messages([*prefix, *history])
        memory = TurnMemory()
        stop_reason: StopReason = "answer"
        try:
            output: object = await self._agent(context).run(
                user_msg=message,
                chat_history=seed,
                memory=memory,
                max_iterations=deps.spec.max_iterations + 1,
            )
            assert isinstance(output, AgentOutput)
            answer = output.response.content or ""
            turn = [
                replace(m, cache_checkpoint=False)
                for m in to_chat_messages((await memory.aget_all())[len(seed) :])
            ]
        except WorkflowRuntimeError as exc:
            if "Max iterations" not in str(exc):
                raise
            # Las tools de este turno quedan en el scratchpad del workflow, que no se persiste:
            # el historial guarda lo que el cliente vio.
            stop_reason, answer = "max_iterations", deps.spec.fallback_answer
            turn = [ChatMessage(Role.USER, message), ChatMessage(Role.ASSISTANT, answer)]
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


def create(deps: AgentDeps) -> LlamaIndexAgent:
    return LlamaIndexAgent(deps)

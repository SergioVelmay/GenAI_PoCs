"""Sabor carril C: Google ADK (Agent Development Kit), el SDK nativo de GCP (7.G2).

`LlmAgent` + `Runner`: el bucle (modelo → tools en paralelo → modelo) es de ADK y el estado de
la conversación vive en un `SessionService` como lista de eventos.
- Límite: `RunConfig(max_llm_calls=N)` tiene la semántica del bucle native (N llamadas, se
  ejecutan las tools de cada una), pero la llamada N + 1 lanza `LlmCallsLimitExceededError`, que
  ADK trata como un fallo del nodo (traza en ERROR e invocación abortada). En su lugar,
  `IterationLimit` (un `before_model_callback`, que corre antes del conteo) responde en la
  llamada N + 1 con el fallback sin llamar al modelo: el turno termina limpio y el fallback queda
  en la sesión como respuesta del agente (como el `ChatMiddleware` de MAF).
- Identidad: la tool se construye por turno con el `ToolContext` en una *closure*. No va en
  `session.state`: ADK sustituye `{clave}` del estado dentro de las instrucciones, así que el
  estado puede acabar en el prompt.
- Instrucciones: con una función (`InstructionProvider`), que ADK no pasa por esas plantillas
  (el prompt y las skills tienen llaves literales).
- Memoria: el hilo vive en nuestro `CheckpointStore` y cada turno siembra una sesión en memoria
  (`InMemorySessionService`) con el historial ya compactado por nuestra política. Las
  alternativas nativas son `VertexAiSessionService` (Sessions de Agent Runtime, 7.G3) y la
  compactación de eventos de la `App` (`EventsCompactionConfig`), que resume *después* de la
  invocación por ventanas de invocaciones: ver la comparativa en el doc de la fase 7.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.events import Event
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext as AdkToolContext
from google.genai import types

from booking_agent.bridges.adk import (
    GatewayLlm,
    arguments_of,
    to_chat_messages,
    to_contents,
    tool_result,
)
from booking_agent.runtime import AgentDeps, AgentTurn, JournaledExecutor, StopReason
from genaipocs_core.application.agents import (
    ConversationSummarizer,
    ShortTermMemory,
    build_prompt_prefix,
)
from genaipocs_core.application.agents.context import load_thread
from genaipocs_core.application.tools import Tool, ToolContext
from genaipocs_core.ports import Role, ToolCall
from genaipocs_core.ports.memory import ThreadState

APP_NAME = "genaipocs"
# ADK exige un identificador de Python como nombre del agente.
AGENT_NAME = "genaipocs_booking_agent"


class JournaledTool(BaseTool):
    """Tool de ADK que delega en el ejecutor común (validación, timeouts, diario)."""

    def __init__(self, tool: Tool[Any], executor: JournaledExecutor, context: ToolContext):
        spec = tool.spec()
        super().__init__(name=spec.name, description=spec.description)
        self._schema = dict(spec.json_schema)
        self._executor = executor
        self._context = context

    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name, description=self.description, parameters_json_schema=self._schema
        )

    async def run_async(self, *, args: dict[str, Any], tool_context: AdkToolContext) -> Any:
        # A diferencia de MAF y LlamaIndex, ADK sí da el id de la llamada al modelo.
        call = ToolCall(tool_context.function_call_id or "", self.name, arguments_of(args))
        execution = await self._executor.execute(call, self._context)
        return tool_result(execution.output, is_error=execution.is_error)


class IterationLimit:
    """`before_model_callback`: a partir de la llamada N + 1 devuelve el fallback."""

    def __init__(self, max_calls: int, fallback: str) -> None:
        self._max_calls = max_calls
        self._fallback = fallback
        self._calls = 0
        self.reached = False

    def __call__(self, _: CallbackContext, llm_request: LlmRequest) -> LlmResponse | None:
        self._calls += 1
        if self._calls <= self._max_calls:
            return None
        self.reached = True
        return LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self._fallback)])
        )


class AdkAgent:
    flavor = "adk"

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps
        policy = deps.spec.context
        summarizer = ConversationSummarizer(deps.summarizer or deps.llm, policy)
        self._memory = ShortTermMemory(policy, summarizer if policy.summarize else None)
        self._specs = [tool.spec() for tool in deps.spec.tools]

    def _instruction(self, _: ReadonlyContext) -> str:
        return self._deps.spec.system_prompt

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
        limit = IterationLimit(deps.spec.max_iterations, deps.spec.fallback_answer)
        agent = LlmAgent(
            name=AGENT_NAME,
            model=GatewayLlm(deps.llm, self._specs, prefix=prefix, history=len(history)),
            instruction=self._instruction,
            tools=[JournaledTool(tool, deps.executor, context) for tool in deps.spec.tools],
            before_model_callback=limit,
        )
        sessions = InMemorySessionService()
        session = await sessions.create_session(app_name=APP_NAME, user_id=context.customer_id)
        for index, content in enumerate(to_contents(history)):
            author = "user" if _is_user_input(content) else AGENT_NAME
            event = Event(author=author, invocation_id=f"seed-{index}", content=content)
            await sessions.append_event(session, event)
        seeded = len(session.events)
        runner = Runner(app_name=APP_NAME, agent=agent, session_service=sessions)
        async for _ in runner.run_async(
            user_id=context.customer_id,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=message)]),
        ):
            pass
        current = await sessions.get_session(
            app_name=APP_NAME, user_id=context.customer_id, session_id=session.id
        )
        events = current.events[seeded:] if current is not None else []
        turn = to_chat_messages(e.content for e in events if e.content is not None)
        stop_reason: StopReason = "max_iterations" if limit.reached else "answer"
        answer = turn[-1].content if turn and turn[-1].role is Role.ASSISTANT else ""
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


def _is_user_input(content: types.Content) -> bool:
    """Lo que escribe el cliente; los resultados de tools (también `user`) son del agente."""
    parts = content.parts or []
    return content.role == "user" and not any(p.function_response for p in parts)


def create(deps: AgentDeps) -> AdkAgent:
    return AdkAgent(deps)

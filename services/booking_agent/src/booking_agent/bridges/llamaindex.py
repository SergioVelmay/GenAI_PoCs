"""`FunctionCallingLLM` de LlamaIndex sobre `LLMPort`.

Convenciones del puente en `ChatMessage.additional_kwargs` de LlamaIndex:
- ASSISTANT: `tool_calls` = [{"id", "name", "arguments"}] (arguments en JSON, como el proveedor).
- TOOL: `tool_call_id` (lo pone el propio `FunctionAgent`) e `is_error`.
- Cualquiera: `cache_checkpoint`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from llama_index.core.base.llms.types import (
    ChatMessage as LIChatMessage,
)
from llama_index.core.base.llms.types import (
    ChatResponse,
    ChatResponseAsyncGen,
    ChatResponseGen,
    CompletionResponse,
    CompletionResponseAsyncGen,
    CompletionResponseGen,
    LLMMetadata,
    MessageRole,
)
from llama_index.core.llms.function_calling import FunctionCallingLLM
from llama_index.core.llms.llm import ToolSelection
from llama_index.core.tools.types import BaseTool
from pydantic import PrivateAttr

from headache_core.ports import ChatMessage, LLMPort, Role, ToolCall, ToolConfig
from headache_core.ports.llm import ToolSpec

CACHE_CHECKPOINT = "cache_checkpoint"
# Argumentos que no eran un objeto JSON: viajan crudos hasta el ejecutor, que devuelve el error.
RAW_ARGUMENTS = "__raw_arguments__"

_TO_ROLE = {
    MessageRole.SYSTEM: Role.SYSTEM,
    MessageRole.USER: Role.USER,
    MessageRole.ASSISTANT: Role.ASSISTANT,
    MessageRole.TOOL: Role.TOOL,
}
_FROM_ROLE = {v: k for k, v in _TO_ROLE.items()}


def to_chat_messages(messages: Sequence[LIChatMessage]) -> list[ChatMessage]:
    converted: list[ChatMessage] = []
    for m in messages:
        role = _TO_ROLE.get(m.role)
        if role is None:
            raise TypeError(f"Rol de LlamaIndex no soportado: {m.role}")
        extra = m.additional_kwargs
        calls = tuple(
            ToolCall(c["id"], c["name"], c["arguments"]) for c in extra.get("tool_calls", [])
        )
        converted.append(
            ChatMessage(
                role,
                m.content or "",
                cache_checkpoint=bool(extra.get(CACHE_CHECKPOINT, False)),
                tool_calls=calls,
                tool_call_id=extra.get("tool_call_id"),
                is_error=bool(extra.get("is_error", False)),
            )
        )
    return converted


def to_llama_messages(messages: Sequence[ChatMessage]) -> list[LIChatMessage]:
    converted: list[LIChatMessage] = []
    for m in messages:
        extra: dict[str, Any] = {}
        if m.cache_checkpoint:
            extra[CACHE_CHECKPOINT] = True
        if m.tool_calls:
            extra["tool_calls"] = [
                {"id": c.id, "name": c.name, "arguments": c.arguments} for c in m.tool_calls
            ]
        if m.tool_call_id is not None:
            extra["tool_call_id"] = m.tool_call_id
        if m.is_error:
            extra["is_error"] = True
        converted.append(
            LIChatMessage(role=_FROM_ROLE[m.role], content=m.content, additional_kwargs=extra)
        )
    return converted


class GatewayFunctionLLM(FunctionCallingLLM):
    _gateway: LLMPort = PrivateAttr()
    _specs: dict[str, ToolSpec] = PrivateAttr(default_factory=dict)

    def __init__(self, gateway: LLMPort, tool_specs: Iterable[ToolSpec] = (), **kwargs: Any):
        super().__init__(**kwargs)
        self._gateway = gateway
        self._specs = {spec.name: spec for spec in tool_specs}

    @classmethod
    def class_name(cls) -> str:
        return "HeadacheGatewayLLM"

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(
            model_name="headache-gateway", is_chat_model=True, is_function_calling_model=True
        )

    def _spec_for(self, tool: BaseTool) -> ToolSpec:
        name = tool.metadata.get_name()
        if (spec := self._specs.get(name)) is not None:
            return spec
        # Tool ajena al spec (p. ej. añadida por el framework): esquema que infiere LlamaIndex.
        return ToolSpec(
            name, tool.metadata.description, tool.metadata.get_parameters_dict(), strict=False
        )

    def _prepare_chat_with_tools(
        self,
        tools: Sequence[BaseTool],
        user_msg: str | LIChatMessage | None = None,
        chat_history: list[LIChatMessage] | None = None,
        verbose: bool = False,
        allow_parallel_tool_calls: bool = False,
        tool_required: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        messages = list(chat_history or [])
        if isinstance(user_msg, str):
            user_msg = LIChatMessage(role=MessageRole.USER, content=user_msg)
        if user_msg is not None:
            messages.append(user_msg)
        specs = tuple(self._spec_for(tool) for tool in tools)
        config = (
            ToolConfig(
                specs,
                choice="required" if tool_required else "auto",
                parallel=allow_parallel_tool_calls,
            )
            if specs
            else None
        )
        return {"messages": messages, "tool_config": config}

    def get_tool_calls_from_response(
        self, response: ChatResponse, error_on_no_tool_call: bool = True, **kwargs: Any
    ) -> list[ToolSelection]:
        raw_calls = response.message.additional_kwargs.get("tool_calls", [])
        if not raw_calls and error_on_no_tool_call:
            raise ValueError("La respuesta no contiene llamadas a tools.")
        selections: list[ToolSelection] = []
        for call in raw_calls:
            try:
                args = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                args = None
            if not isinstance(args, dict):
                args = {RAW_ARGUMENTS: call["arguments"]}
            selections.append(
                ToolSelection(tool_id=call["id"], tool_name=call["name"], tool_kwargs=args)
            )
        return selections

    async def achat(self, messages: Sequence[LIChatMessage], **kwargs: Any) -> ChatResponse:
        config: ToolConfig | None = kwargs.get("tool_config")
        response = await self._gateway.complete(to_chat_messages(messages), tools=config)
        [message] = to_llama_messages(
            [ChatMessage(Role.ASSISTANT, response.content, tool_calls=response.tool_calls)]
        )
        return ChatResponse(
            message=message,
            raw={"model": response.model, "finish_reason": response.finish_reason},
            additional_kwargs={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "cached_tokens": response.usage.cached_input_tokens,
            },
        )

    # El gateway es asíncrono y sin streaming: el agente se crea con `streaming=False`.
    def chat(self, messages: Sequence[LIChatMessage], **kwargs: Any) -> ChatResponse:
        raise NotImplementedError("Usa achat: el gateway es asíncrono.")

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        raise NotImplementedError("El agente solo usa chat.")

    def stream_chat(self, messages: Sequence[LIChatMessage], **kwargs: Any) -> ChatResponseGen:
        raise NotImplementedError("Sin streaming en el puente.")

    def stream_complete(
        self, prompt: str, formatted: bool = False, **kwargs: Any
    ) -> CompletionResponseGen:
        raise NotImplementedError("Sin streaming en el puente.")

    async def acomplete(
        self, prompt: str, formatted: bool = False, **kwargs: Any
    ) -> CompletionResponse:
        raise NotImplementedError("El agente solo usa chat.")

    async def astream_chat(
        self, messages: Sequence[LIChatMessage], **kwargs: Any
    ) -> ChatResponseAsyncGen:
        raise NotImplementedError("Sin streaming en el puente.")

    async def astream_complete(
        self, prompt: str, formatted: bool = False, **kwargs: Any
    ) -> CompletionResponseAsyncGen:
        raise NotImplementedError("Sin streaming en el puente.")

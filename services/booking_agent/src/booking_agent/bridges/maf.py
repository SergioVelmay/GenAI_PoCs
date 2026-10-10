"""Cliente de chat de Microsoft Agent Framework sobre `LLMPort`.

MAF compone el cliente por capas: `FunctionInvocationLayer` (el bucle de tools vive en el
cliente, no en el agente) → `ChatMiddlewareLayer` → `BaseChatClient` (la llamada al modelo).
Aquí la capa base es nuestro gateway; el resto es MAF tal cual.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
)
from agent_framework._types import UsageDetails

from headache_core.ports import ChatMessage, LLMPort, LLMResponse, Role, ToolCall, ToolConfig
from headache_core.ports.llm import ToolSpec

CACHE_CHECKPOINT = "cache_checkpoint"
# MAF parsea y valida los argumentos antes de llamar a la función: el JSON inválido viaja
# envuelto para que lo rechace nuestro ejecutor (y quede en el diario) igual que en native.
RAW_ARGUMENTS = "__raw_arguments__"


def _arguments(call: Content) -> str:
    raw = call.arguments
    if isinstance(raw, Mapping):
        if isinstance(wrapped := raw.get(RAW_ARGUMENTS), str):
            return wrapped
        return json.dumps(dict(raw), ensure_ascii=False)
    return raw or ""


def _result_text(result: Any) -> str:
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def to_chat_messages(messages: Sequence[Message]) -> list[ChatMessage]:
    converted: list[ChatMessage] = []
    for m in messages:
        checkpoint = bool((m.additional_properties or {}).get(CACHE_CHECKPOINT, False))
        if m.role == "tool":
            converted.extend(
                ChatMessage(
                    Role.TOOL,
                    _result_text(c.result),
                    tool_call_id=c.call_id,
                    is_error=c.exception is not None,
                )
                for c in m.contents
                if c.type == "function_result"
            )
            continue
        calls = tuple(
            ToolCall(c.call_id or "", c.name or "", _arguments(c))
            for c in m.contents
            if c.type == "function_call"
        )
        converted.append(ChatMessage(Role(m.role), m.text, checkpoint, tool_calls=calls))
    return converted


def _call_content(call: ToolCall) -> Content:
    try:
        args = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        args = None
    if not isinstance(args, dict):
        args = {RAW_ARGUMENTS: call.arguments}
    return Content.from_function_call(call.id, call.name, arguments=args)


def to_maf_messages(messages: Iterable[ChatMessage]) -> list[Message]:
    converted: list[Message] = []
    for m in messages:
        extra = {CACHE_CHECKPOINT: True} if m.cache_checkpoint else None
        if m.role is Role.TOOL:
            result = Content.from_function_result(
                m.tool_call_id or "", result=m.content, exception="error" if m.is_error else None
            )
            converted.append(Message("tool", [result], additional_properties=extra))
            continue
        contents: list[Content | str] = [m.content] if m.content else []
        contents += [_call_content(c) for c in m.tool_calls]
        converted.append(Message(m.role.value, contents, additional_properties=extra))
    return converted


def _usage(response: LLMResponse) -> UsageDetails:
    usage = response.usage
    return UsageDetails(
        input_token_count=usage.input_tokens,
        output_token_count=usage.output_tokens,
        total_token_count=usage.total_tokens,
        cache_read_input_token_count=usage.cached_input_tokens,
        cache_creation_input_token_count=usage.cache_write_tokens,
    )


class GatewayChatClient(
    FunctionInvocationLayer[Any], ChatMiddlewareLayer[Any], BaseChatClient[Any]
):
    OTEL_PROVIDER_NAME = "headache-gateway"

    def __init__(self, gateway: LLMPort, tool_specs: Iterable[ToolSpec], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._gateway = gateway
        self._specs = {spec.name: spec for spec in tool_specs}

    def _tool_config(self, options: Mapping[str, Any]) -> ToolConfig | None:
        tools = options.get("tools") or []
        choice = options.get("tool_choice")
        if not tools or choice == "none":
            return None
        # Se envía el ToolSpec del agente, no el esquema permisivo con el que se registró en MAF.
        specs = tuple(self._specs[t.name] for t in tools if t.name in self._specs)
        return ToolConfig(specs, choice="required" if choice == "required" else "auto")

    async def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> ChatResponse[Any]:
        if stream:
            raise NotImplementedError("El puente no hace streaming.")
        prompt = to_chat_messages(messages)
        if instructions := options.get("instructions"):
            # Las instrucciones del agente son el prefijo estable: primer checkpoint de caché.
            prompt.insert(0, ChatMessage(Role.SYSTEM, instructions, cache_checkpoint=True))
        response = await self._gateway.complete(prompt, tools=self._tool_config(options))
        [reply] = to_maf_messages(
            [ChatMessage(Role.ASSISTANT, response.content, tool_calls=response.tool_calls)]
        )
        return ChatResponse(
            messages=[reply],
            model=response.model,
            finish_reason="tool_calls" if response.tool_calls else "stop",
            usage_details=_usage(response),
        )

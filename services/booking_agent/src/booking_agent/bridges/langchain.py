"""`BaseChatModel` de LangChain sobre `LLMPort`.

Cualquier pieza del ecosistema (LangGraph, `create_agent`, middleware) puede usarlo como un chat
model más. `bind_tools` acepta el formato OpenAI y lo traduce a nuestros `ToolSpec` sin perder
`strict`, así el modelo ve exactamente los mismos esquemas que en el sabor native.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    InvalidToolCall,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages import ToolCall as LCToolCall
from langchain_core.messages.ai import InputTokenDetails, UsageMetadata
from langchain_core.messages.tool import invalid_tool_call, tool_call
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, SkipValidation

from headache_core.ports import ChatMessage, LLMPort, LLMResponse, Role, ToolCall, ToolConfig
from headache_core.ports.llm import ToolSpec

# LangChain no tiene noción de checkpoint de caché: viaja en `additional_kwargs`.
CACHE_CHECKPOINT = "cache_checkpoint"


def spec_to_openai(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": dict(spec.json_schema),
            "strict": spec.strict,
        },
    }


def _openai_to_spec(tool: dict[str, Any]) -> ToolSpec:
    function = tool["function"]
    return ToolSpec(
        name=function["name"],
        description=function.get("description", ""),
        json_schema=function.get("parameters", {}),
        strict=bool(function.get("strict", False)),
    )


def system_message(message: ChatMessage) -> SystemMessage:
    return SystemMessage(
        message.content, additional_kwargs={CACHE_CHECKPOINT: message.cache_checkpoint}
    )


def with_cache_checkpoint(message: BaseMessage) -> BaseMessage:
    kwargs = {**message.additional_kwargs, CACHE_CHECKPOINT: True}
    return message.model_copy(update={"additional_kwargs": kwargs})


def tool_calls_of(message: AIMessage) -> tuple[ToolCall, ...]:
    valid = [
        ToolCall(c["id"] or "", c["name"], json.dumps(c["args"], ensure_ascii=False))
        for c in message.tool_calls
    ]
    # Argumentos que no eran JSON: se reenvían tal cual para que el ejecutor devuelva el error.
    invalid = [
        ToolCall(c["id"] or "", c["name"] or "", c["args"] or "")
        for c in message.invalid_tool_calls
    ]
    return (*valid, *invalid)


def to_chat_messages(messages: Sequence[BaseMessage]) -> list[ChatMessage]:
    converted: list[ChatMessage] = []
    for m in messages:
        checkpoint = bool(m.additional_kwargs.get(CACHE_CHECKPOINT, False))
        if isinstance(m, SystemMessage):
            converted.append(ChatMessage(Role.SYSTEM, m.text, cache_checkpoint=checkpoint))
        elif isinstance(m, HumanMessage):
            converted.append(ChatMessage(Role.USER, m.text, cache_checkpoint=checkpoint))
        elif isinstance(m, AIMessage):
            converted.append(
                ChatMessage(
                    Role.ASSISTANT,
                    m.text,
                    cache_checkpoint=checkpoint,
                    tool_calls=tool_calls_of(m),
                )
            )
        elif isinstance(m, ToolMessage):
            converted.append(
                ChatMessage(
                    Role.TOOL,
                    m.text,
                    cache_checkpoint=checkpoint,
                    tool_call_id=m.tool_call_id,
                    is_error=m.status == "error",
                )
            )
        else:
            raise TypeError(f"Mensaje de LangChain no soportado: {type(m).__name__}")
    return converted


def to_ai_message(response: LLMResponse) -> AIMessage:
    valid: list[LCToolCall] = []
    invalid: list[InvalidToolCall] = []
    for call in response.tool_calls:
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            args = None
        if not isinstance(args, dict):
            invalid.append(
                invalid_tool_call(
                    name=call.name, args=call.arguments, id=call.id, error="JSON no válido"
                )
            )
            continue
        valid.append(tool_call(name=call.name, args=args, id=call.id))
    usage = response.usage
    return AIMessage(
        content=response.content,
        tool_calls=valid,
        invalid_tool_calls=invalid,
        usage_metadata=UsageMetadata(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            input_token_details=InputTokenDetails(
                cache_read=usage.cached_input_tokens, cache_creation=usage.cache_write_tokens
            ),
        ),
        response_metadata={
            "model_name": response.model,
            "model_provider": response.provider,
            "finish_reason": response.finish_reason,
        },
    )


class GatewayChatModel(BaseChatModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # `LLMPort` es un Protocol: Pydantic no puede validarlo, se confía en el tipo estático.
    gateway: SkipValidation[LLMPort]
    tool_specs: tuple[ToolSpec, ...] = ()
    tool_choice: str = "auto"
    temperature: float = 0.0

    @property
    def _llm_type(self) -> str:
        return "headache-gateway"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        specs = tuple(_openai_to_spec(convert_to_openai_tool(t)) for t in tools)
        return self.model_copy(update={"tool_specs": specs, "tool_choice": tool_choice or "auto"})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("El gateway es asíncrono: usa ainvoke/astream.")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        tools = ToolConfig(self.tool_specs, choice=self.tool_choice) if self.tool_specs else None
        response = await self.gateway.complete(
            to_chat_messages(messages), temperature=self.temperature, tools=tools
        )
        return ChatResult(generations=[ChatGeneration(message=to_ai_message(response))])

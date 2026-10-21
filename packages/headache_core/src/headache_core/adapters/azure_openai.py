"""Adaptador Azure OpenAI (Foundry) con el SDK oficial `openai`.

Prompt caching: automático para prefijos idénticos de >= 1024 tokens; se lee en
`usage.prompt_tokens_details.cached_tokens`. No hay que marcar nada (ni se puede desactivar).
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Mapping, Sequence
from typing import Self

import openai
from openai import AsyncAzureOpenAI, AsyncOpenAI, omit
from openai.types.chat import (
    ChatCompletionFunctionToolParam,
    ChatCompletionMessage,
    ChatCompletionMessageFunctionToolCall,
    ChatCompletionMessageParam,
    ChatCompletionToolChoiceOptionParam,
)
from openai.types.completion_usage import CompletionUsage
from openai.types.shared_params import ResponseFormatJSONSchema

from headache_core.config import AzureOpenAISettings
from headache_core.ports import (
    ChatMessage,
    LLMContentFilterError,
    LLMError,
    LLMRateLimitError,
    LLMResponse,
    LLMStreamEvent,
    LLMTransientError,
    ResponseSchema,
    Role,
    TextDelta,
    TokenUsage,
    ToolCall,
    ToolConfig,
)

COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


def retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    """`retry-after-ms` (Azure OpenAI) o `retry-after` en segundos; la forma de fecha se ignora."""
    for name, scale in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        value = headers.get(name)
        if value is None:
            continue
        try:
            seconds = float(value) / scale
        except ValueError:
            continue
        if seconds >= 0:
            return seconds
    return None


def to_llm_error(exc: openai.OpenAIError) -> LLMError:
    """Traduce errores del SDK de OpenAI (también los de LiteLLM, que heredan de ellos)."""
    retry_after = (
        retry_after_seconds(exc.response.headers)
        if isinstance(exc, openai.APIStatusError)
        else None
    )
    if isinstance(exc, openai.RateLimitError):
        return LLMRateLimitError(str(exc), retry_after=retry_after)
    if isinstance(exc, openai.APIConnectionError):  # Incluye APITimeoutError.
        return LLMTransientError(str(exc))
    if isinstance(exc, openai.APIStatusError) and (
        exc.status_code >= 500 or exc.status_code == 408
    ):
        return LLMTransientError(str(exc), retry_after=retry_after)
    if isinstance(exc, openai.BadRequestError) and exc.code == "content_filter":
        return LLMContentFilterError(str(exc))
    return LLMError(str(exc))


def _to_openai(message: ChatMessage) -> ChatCompletionMessageParam:
    match message.role:
        case Role.SYSTEM:
            return {"role": "system", "content": message.content}
        case Role.USER:
            return {"role": "user", "content": message.content}
        case Role.ASSISTANT if message.tool_calls:
            return {
                "role": "assistant",
                "content": message.content or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                    for call in message.tool_calls
                ],
            }
        case Role.ASSISTANT:
            return {"role": "assistant", "content": message.content}
        case Role.TOOL:
            if message.tool_call_id is None:
                raise LLMError("Un mensaje TOOL necesita tool_call_id.")
            # OpenAI no tiene status de error: el contenido ya lo describe.
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }


def to_openai_tools(config: ToolConfig) -> list[ChatCompletionFunctionToolParam]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": dict(spec.json_schema),
                "strict": spec.strict,
            },
        }
        for spec in config.tools
    ]


def to_openai_tool_choice(config: ToolConfig) -> ChatCompletionToolChoiceOptionParam:
    if config.choice == "auto":
        return "auto"
    if config.choice == "required":
        return "required"
    return {"type": "function", "function": {"name": config.choice}}


def _tool_calls(message: ChatCompletionMessage) -> tuple[ToolCall, ...]:
    return tuple(
        ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments)
        for call in message.tool_calls or ()
        if isinstance(call, ChatCompletionMessageFunctionToolCall)
    )


def _response_format(schema: ResponseSchema) -> ResponseFormatJSONSchema:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.name,
            "description": schema.description,
            "schema": dict(schema.json_schema),
            "strict": True,
        },
    }


def create_client(settings: AzureOpenAISettings) -> AsyncOpenAI:
    if not settings.endpoint:
        raise LLMError("Falta AZURE_OPENAI__ENDPOINT.")
    # max_retries=0: los reintentos y el fallback son responsabilidad del gateway.
    if settings.api_key is not None and settings.api_key.get_secret_value():
        return AsyncAzureOpenAI(
            azure_endpoint=settings.endpoint,
            api_key=settings.api_key.get_secret_value(),
            api_version=settings.api_version,
            max_retries=0,
        )
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    return AsyncAzureOpenAI(
        azure_endpoint=settings.endpoint,
        azure_ad_token_provider=get_bearer_token_provider(
            DefaultAzureCredential(), COGNITIVE_SERVICES_SCOPE
        ),
        api_version=settings.api_version,
        max_retries=0,
    )


class AzureOpenAILLM:
    def __init__(self, client: AsyncOpenAI, deployment: str) -> None:
        self._client = client
        self._deployment = deployment

    @classmethod
    def from_settings(cls, settings: AzureOpenAISettings, *, deployment: str) -> Self:
        return cls(create_client(settings), deployment)

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        started = time.perf_counter()
        try:
            completion = await self._client.chat.completions.create(
                model=self._deployment,
                messages=[_to_openai(m) for m in messages],
                temperature=temperature,
                max_tokens=max_tokens if max_tokens is not None else omit,
                response_format=_response_format(response_schema) if response_schema else omit,
                tools=to_openai_tools(tools) if tools else omit,
                tool_choice=to_openai_tool_choice(tools) if tools else omit,
                parallel_tool_calls=tools.parallel if tools else omit,
            )
        except openai.OpenAIError as exc:
            raise to_llm_error(exc) from exc
        latency_ms = (time.perf_counter() - started) * 1000

        choice = completion.choices[0]
        if choice.message.refusal:
            raise LLMError(f"El modelo rechazó la petición: {choice.message.refusal}")
        usage = completion.usage
        details = usage.prompt_tokens_details if usage else None
        return LLMResponse(
            content=choice.message.content or "",
            model=completion.model,
            usage=TokenUsage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                cached_input_tokens=(details.cached_tokens or 0) if details else 0,
            ),
            provider="azure",
            latency_ms=latency_ms,
            finish_reason=choice.finish_reason,
            tool_calls=_tool_calls(choice.message),
        )

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        """`stream=True` con `include_usage`: el último chunk trae el uso (sin `choices`)."""
        started = time.perf_counter()
        parts: list[str] = []
        model, finish_reason = self._deployment, None
        usage: CompletionUsage | None = None
        try:
            chunks = await self._client.chat.completions.create(
                model=self._deployment,
                messages=[_to_openai(m) for m in messages],
                temperature=temperature,
                max_tokens=max_tokens if max_tokens is not None else omit,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in chunks:
                model = chunk.model or model
                usage = chunk.usage or usage
                # Azure manda chunks sin `choices` (resultados del filtro de contenido y el uso).
                for choice in chunk.choices:
                    if choice.delta.content:
                        parts.append(choice.delta.content)
                        yield TextDelta(choice.delta.content)
                    finish_reason = choice.finish_reason or finish_reason
        except openai.OpenAIError as exc:
            raise to_llm_error(exc) from exc
        if finish_reason == "content_filter":
            raise LLMContentFilterError("El filtro de contenido cortó la respuesta.")
        details = usage.prompt_tokens_details if usage else None
        yield LLMResponse(
            content="".join(parts),
            model=model,
            usage=TokenUsage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                cached_input_tokens=(details.cached_tokens or 0) if details else 0,
            ),
            provider="azure",
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason=finish_reason,
        )

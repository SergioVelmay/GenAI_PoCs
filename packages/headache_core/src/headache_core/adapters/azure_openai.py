"""Adaptador Azure OpenAI (Foundry) con el SDK oficial `openai`.

Prompt caching: automático para prefijos idénticos de >= 1024 tokens; se lee en
`usage.prompt_tokens_details.cached_tokens`. No hay que marcar nada (ni se puede desactivar).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Self

import openai
from openai import AsyncAzureOpenAI, AsyncOpenAI, omit
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONSchema

from headache_core.config import AzureOpenAISettings
from headache_core.ports import (
    ChatMessage,
    LLMError,
    LLMRateLimitError,
    LLMResponse,
    LLMTransientError,
    ResponseSchema,
    Role,
    TokenUsage,
)

COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


def to_llm_error(exc: openai.OpenAIError) -> LLMError:
    """Traduce errores del SDK de OpenAI (también los de LiteLLM, que heredan de ellos)."""
    if isinstance(exc, openai.RateLimitError):
        return LLMRateLimitError(str(exc))
    if isinstance(exc, openai.APIConnectionError):  # Incluye APITimeoutError.
        return LLMTransientError(str(exc))
    if isinstance(exc, openai.APIStatusError) and (
        exc.status_code >= 500 or exc.status_code == 408
    ):
        return LLMTransientError(str(exc))
    return LLMError(str(exc))


def _to_openai(message: ChatMessage) -> ChatCompletionMessageParam:
    match message.role:
        case Role.SYSTEM:
            return {"role": "system", "content": message.content}
        case Role.USER:
            return {"role": "user", "content": message.content}
        case Role.ASSISTANT:
            return {"role": "assistant", "content": message.content}


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
    ) -> LLMResponse:
        started = time.perf_counter()
        try:
            completion = await self._client.chat.completions.create(
                model=self._deployment,
                messages=[_to_openai(m) for m in messages],
                temperature=temperature,
                max_tokens=max_tokens if max_tokens is not None else omit,
                response_format=_response_format(response_schema) if response_schema else omit,
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
        )

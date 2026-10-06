"""Carril C: LiteLLM como LLM Gateway.

Su `Router` resuelve lo mismo que nuestro gateway propio (reintentos, fallback entre
proveedores, límites RPM/TPM) con configuración declarativa. Sus excepciones heredan de las
de `openai`, así que se traducen igual que en el adaptador de Azure.
"""

from __future__ import annotations

import os

# Usa el mapa de precios local de LiteLLM: sin descargas de red al importar (tests deterministas).
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import time
from collections.abc import Sequence
from typing import Any

import openai
from litellm.router import Router

from headache_core.adapters.azure_openai import COGNITIVE_SERVICES_SCOPE, to_llm_error
from headache_core.application.llm import ModelTier
from headache_core.config import LLMProvider, Settings
from headache_core.ports import (
    ChatMessage,
    LLMError,
    LLMResponse,
    ResponseSchema,
    TokenUsage,
)


def model_group(provider: LLMProvider, tier: ModelTier) -> str:
    return f"{provider}-{tier}"


def _litellm_params(settings: Settings, provider: LLMProvider, tier: ModelTier) -> dict[str, Any]:
    gateway = settings.llm_gateway
    limits = {"rpm": gateway.requests_per_minute, "tpm": gateway.tokens_per_minute}
    if provider is LLMProvider.AZURE:
        azure = settings.azure_openai
        deployment = (
            azure.cheap_chat_deployment if tier is ModelTier.CHEAP else azure.chat_deployment
        )
        params: dict[str, Any] = {
            "model": f"azure/{deployment}",
            "api_base": azure.endpoint,
            "api_version": azure.api_version,
            **limits,
        }
        if azure.api_key is not None and azure.api_key.get_secret_value():
            params["api_key"] = azure.api_key.get_secret_value()
        else:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            params["azure_ad_token_provider"] = get_bearer_token_provider(
                DefaultAzureCredential(), COGNITIVE_SERVICES_SCOPE
            )
        return params
    bedrock = settings.bedrock
    model_id = bedrock.cheap_chat_model_id if tier is ModelTier.CHEAP else bedrock.chat_model_id
    params = {"model": f"bedrock/converse/{model_id}", "aws_region_name": bedrock.region, **limits}
    if bedrock.profile:
        params["aws_profile_name"] = bedrock.profile
    return params


def build_router_config(
    settings: Settings,
) -> tuple[list[dict[str, Any]], list[dict[str, list[str]]]]:
    """Un *model group* por proveedor y nivel (`azure-cheap`, `aws-strong`...) y sus fallbacks."""
    chain = settings.provider_chain()
    model_list = [
        {"model_name": model_group(p, t), "litellm_params": _litellm_params(settings, p, t)}
        for p in chain
        for t in ModelTier
    ]
    fallbacks = [
        {model_group(chain[0], t): [model_group(p, t) for p in chain[1:]]}
        for t in ModelTier
        if len(chain) > 1
    ]
    return model_list, fallbacks


def build_router(settings: Settings) -> Router:
    model_list, fallbacks = build_router_config(settings)
    return Router(
        model_list=model_list,
        fallbacks=fallbacks,
        num_retries=settings.llm_gateway.max_retries,
        # Respeta rpm/tpm de cada despliegue: si se supera, salta al fallback en vez de 429.
        routing_strategy="usage-based-routing-v2",
    )


def _to_litellm(message: ChatMessage) -> dict[str, Any]:
    if not message.cache_checkpoint:
        return {"role": message.role.value, "content": message.content}
    # Formato de Anthropic; LiteLLM lo traduce a `cachePoint` en Bedrock y lo ignora en Azure.
    return {
        "role": message.role.value,
        "content": [
            {"type": "text", "text": message.content, "cache_control": {"type": "ephemeral"}}
        ],
    }


class LiteLLMGateway:
    def __init__(self, router: Router, model_group: str) -> None:
        self._router = router
        self._model_group = model_group

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
    ) -> LLMResponse:
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        if response_schema is not None:
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.name,
                    "schema": dict(response_schema.json_schema),
                    "strict": True,
                },
            }
        started = time.perf_counter()
        payload: Any = [_to_litellm(m) for m in messages]
        try:
            response: Any = await self._router.acompletion(
                model=self._model_group,
                messages=payload,
                **options,
            )
        except openai.OpenAIError as exc:
            raise to_llm_error(exc) from exc
        latency_ms = (time.perf_counter() - started) * 1000

        if not response.choices:
            raise LLMError("LiteLLM devolvió una respuesta sin choices.")
        choice = response.choices[0]
        usage = response.usage
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) or getattr(
            usage, "cache_read_input_tokens", 0
        )
        hidden: dict[str, Any] = getattr(response, "_hidden_params", {}) or {}
        return LLMResponse(
            content=choice.message.content or "",
            model=str(response.model),
            usage=TokenUsage(
                input_tokens=int(usage.prompt_tokens),
                output_tokens=int(usage.completion_tokens),
                cached_input_tokens=int(cached or 0),
                cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            ),
            provider=f"litellm:{hidden.get('custom_llm_provider', 'unknown')}",
            latency_ms=latency_ms,
            finish_reason=choice.finish_reason,
        )

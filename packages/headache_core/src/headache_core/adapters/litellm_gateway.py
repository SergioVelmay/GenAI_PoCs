"""Carril C: LiteLLM como LLM Gateway.

Su `Router` resuelve lo mismo que nuestro gateway propio (reintentos, fallback en cadena entre
Azure, Bedrock y Gemini, límites RPM/TPM) con configuración declarativa. Sus excepciones heredan
de las de `openai`, así que se traducen igual que en el adaptador de Azure.
"""

from __future__ import annotations

import os

# Usa el mapa de precios local de LiteLLM: sin descargas de red al importar (tests deterministas).
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
# En modo DEV (por defecto), `import litellm` vuelca el `.env` en `os.environ` de todo el
# proceso: pisaba la configuración (Pydantic Settings) y rompía el aislamiento de los tests.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

import logging
import time
from collections.abc import Sequence
from typing import Any

import openai
from litellm.litellm_core_utils.prompt_templates.factory import (
    THOUGHT_SIGNATURE_SEPARATOR as THOUGHT_SEPARATOR,
)
from litellm.router import Router

from headache_core.adapters.azure_openai import (
    COGNITIVE_SERVICES_SCOPE,
    to_llm_error,
    to_openai_tool_choice,
    to_openai_tools,
)
from headache_core.application.llm import ModelTier
from headache_core.config import LLMProvider, Settings
from headache_core.ports import (
    ChatMessage,
    LLMError,
    LLMResponse,
    ResponseSchema,
    Role,
    TokenUsage,
    ToolCall,
    ToolConfig,
)

logger = logging.getLogger(__name__)


def model_group(provider: LLMProvider, tier: ModelTier) -> str:
    return f"{provider}-{tier}"


def _gemini_params(settings: Settings, tier: ModelTier, limits: dict[str, int]) -> dict[str, Any]:
    """`vertex_ai/` (Agent Platform, credenciales por ADC) o `gemini/` (Developer API con clave).

    `thinking_level` viaja como `reasoning_effort` del despliegue: LiteLLM lo traduce al
    `thinkingLevel` de Gemini 3+. La temperatura del puerto sí la reenvía (con un aviso de
    obsoleta); el modelo la ignora.
    """
    gemini = settings.gemini
    cheap = tier is ModelTier.CHEAP
    model = gemini.cheap_chat_model if cheap else gemini.chat_model
    level = gemini.cheap_thinking_level if cheap else gemini.thinking_level
    params: dict[str, Any] = {**limits}
    if level:
        params["reasoning_effort"] = level
    if not gemini.enterprise:
        key = gemini.api_key.get_secret_value() if gemini.api_key else ""
        if not key:
            raise LLMError("Falta GEMINI__API_KEY (o usa GEMINI__ENTERPRISE=true).")
        return {"model": f"gemini/{model}", "api_key": key, **params}
    if not settings.gcp.project:
        raise LLMError("Falta GCP__PROJECT (Gemini en Agent Platform).")
    if settings.gcp.impersonate_service_account:
        # LiteLLM solo acepta credenciales en JSON, no un objeto: suplanta quien configure ADC.
        logger.warning(
            "LiteLLM no aplica GCP__IMPERSONATE_SERVICE_ACCOUNT: usa la identidad de ADC "
            "(gcloud auth application-default login --impersonate-service-account=...)."
        )
    return {
        "model": f"vertex_ai/{model}",
        "vertex_project": settings.gcp.project,
        "vertex_location": gemini.location,
        **params,
    }


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
    if provider is LLMProvider.GCP:
        return _gemini_params(settings, tier, limits)
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


def _to_litellm_call(call: ToolCall) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": call.arguments},
    }
    if call.signature:
        # Donde LiteLLM busca la firma de Gemini; sin ella pone su valor ficticio.
        payload["provider_specific_fields"] = {"thought_signature": call.signature}
    return payload


def _from_litellm_call(call: Any) -> ToolCall:
    """LiteLLM incrusta la firma de Gemini en el id (`<id>__thought__<firma>`) para clientes
    OpenAI y también la deja en `provider_specific_fields`. En el puerto va aparte: un id con
    ~100 caracteres de base64 rompería el fallback (Bedrock solo admite `[a-zA-Z0-9_-]`)."""
    call_id, _, embedded = str(call.id).partition(THOUGHT_SEPARATOR)
    fields = getattr(call, "provider_specific_fields", None) or getattr(
        call.function, "provider_specific_fields", None
    )
    signature = fields.get("thought_signature") if isinstance(fields, dict) else None
    return ToolCall(
        id=call_id,
        name=call.function.name,
        arguments=call.function.arguments,
        signature=signature or embedded or None,
    )


def _to_litellm(message: ChatMessage) -> dict[str, Any]:
    if message.role is Role.TOOL:
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    if message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [_to_litellm_call(c) for c in message.tool_calls],
        }
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
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        if tools is not None:
            options["tools"] = to_openai_tools(tools)
            options["tool_choice"] = to_openai_tool_choice(tools)
            options["parallel_tool_calls"] = tools.parallel
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
            tool_calls=tuple(
                _from_litellm_call(c) for c in getattr(choice.message, "tool_calls", None) or ()
            ),
        )

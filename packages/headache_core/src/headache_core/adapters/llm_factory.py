"""Raíz de composición del gateway: decide qué adaptadores y capas se montan según `Settings`."""

from __future__ import annotations

from headache_core.application.llm import (
    FallbackLLM,
    HeuristicClassifier,
    LLMClassifier,
    ModelTier,
    NullTracer,
    ObservedLLM,
    PriceTable,
    RateLimitedLLM,
    RoutingLLM,
)
from headache_core.config import LLMProvider, Settings
from headache_core.ports import LLMPort, LLMTracer


def build_provider_llm(settings: Settings, provider: LLMProvider, tier: ModelTier) -> LLMPort:
    """Un modelo concreto de un proveedor, sin capas (carril B: SDK nativo)."""
    if provider is LLMProvider.AZURE:
        from headache_core.adapters.azure_openai import AzureOpenAILLM

        azure = settings.azure_openai
        deployment = (
            azure.cheap_chat_deployment if tier is ModelTier.CHEAP else azure.chat_deployment
        )
        return AzureOpenAILLM.from_settings(azure, deployment=deployment)

    from headache_core.adapters.bedrock import BedrockConverseLLM

    bedrock = settings.bedrock
    model_id = bedrock.cheap_chat_model_id if tier is ModelTier.CHEAP else bedrock.chat_model_id
    return BedrockConverseLLM.from_settings(bedrock, model_id=model_id)


def build_tracer(settings: Settings) -> LLMTracer:
    """Langfuse si hay claves configuradas; si no, no se traza."""
    if settings.langfuse.public_key and settings.langfuse.secret_key.get_secret_value():
        from headache_core.adapters.langfuse_tracer import LangfuseTracer

        return LangfuseTracer.from_settings(settings.langfuse)
    return NullTracer()


def _native_tier(
    settings: Settings, tier: ModelTier, tracer: LLMTracer, prices: PriceTable
) -> LLMPort:
    gateway = settings.llm_gateway
    candidates: list[LLMPort] = [
        ObservedLLM(
            RateLimitedLLM(
                build_provider_llm(settings, provider, tier),
                requests_per_minute=gateway.requests_per_minute,
                tokens_per_minute=gateway.tokens_per_minute,
            ),
            tracer=tracer,
            prices=prices,
            name=f"{provider}:{tier}",
            metadata={"engine": "native", "provider": provider, "tier": tier},
        )
        for provider in settings.provider_chain()
    ]
    return FallbackLLM(candidates, max_retries=gateway.max_retries)


def _litellm_routes(
    settings: Settings, tracer: LLMTracer, prices: PriceTable
) -> dict[ModelTier, LLMPort]:
    from headache_core.adapters.litellm_gateway import LiteLLMGateway, build_router, model_group

    router = build_router(settings)
    return {
        tier: ObservedLLM(
            LiteLLMGateway(router, model_group(settings.llm_provider, tier)),
            tracer=tracer,
            prices=prices,
            name=f"litellm:{tier}",
            metadata={"engine": "litellm", "tier": tier},
        )
        for tier in ModelTier
    }


def build_gateway(settings: Settings, *, tracer: LLMTracer | None = None) -> LLMPort:
    """Gateway completo: routing barato/potente + fallback + rate limit + trazas y coste."""
    tracer = tracer or build_tracer(settings)
    prices = PriceTable()
    routes: dict[ModelTier, LLMPort] = (
        _litellm_routes(settings, tracer, prices)
        if settings.llm_gateway.engine == "litellm"
        else {tier: _native_tier(settings, tier, tracer, prices) for tier in ModelTier}
    )
    classifier = (
        LLMClassifier(routes[ModelTier.CHEAP])
        if settings.llm_gateway.router == "llm"
        else HeuristicClassifier()
    )
    return RoutingLLM(routes, classifier)

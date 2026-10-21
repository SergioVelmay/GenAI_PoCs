"""Raíz de composición del gateway: decide qué adaptadores y capas se montan según `Settings`."""

from __future__ import annotations

from headache_core.application.llm import (
    CircuitBreakerLLM,
    ConcurrencyLimitedLLM,
    FallbackLLM,
    HeuristicClassifier,
    LLMClassifier,
    ModelTier,
    NullTracer,
    ObservedLLM,
    PriceTable,
    RateLimitedLLM,
    RoutingLLM,
    SharedQuotaLLM,
)
from headache_core.application.telemetry import TelemetryLLMTracer
from headache_core.config import LLMProvider, Settings
from headache_core.ports import LLMPort, LLMTracer, RateLimiterPort


def build_provider_llm(settings: Settings, provider: LLMProvider, tier: ModelTier) -> LLMPort:
    """Un modelo concreto de un proveedor, sin capas (carril B: SDK nativo)."""
    if provider is LLMProvider.SIMULATED:
        from headache_core.adapters.simulated_llm import SimulatedLLM

        # Una cuota por "despliegue" (nivel), compartida entre réplicas si hay Redis.
        return SimulatedLLM(settings.simulated_llm, quota=shared_limiter(settings), key=tier)
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
    """OpenTelemetry si está activo (fase 11); si no, el SDK de Langfuse si hay claves; si no,
    no se traza.

    Con OTel no se usa también el SDK de Langfuse: el collector ya le reenvía los spans y cada
    llamada saldría dos veces.
    """
    if settings.telemetry.exporter != "none":
        return TelemetryLLMTracer(capture_content=settings.telemetry.capture_content)
    if settings.langfuse.public_key and settings.langfuse.secret_key.get_secret_value():
        from headache_core.adapters.langfuse_tracer import LangfuseTracer

        return LangfuseTracer.from_settings(settings.langfuse)
    return NullTracer()


def _resilient(
    settings: Settings,
    llm: LLMPort,
    *,
    tracer: LLMTracer,
    prices: PriceTable,
    name: str,
    metadata: dict[str, str],
    limiter: RateLimiterPort | None,
) -> LLMPort:
    """Un despliegue con su pila de resiliencia (fase 10.1).

    Breaker fuera: con el circuito abierto no se gasta cuota ni hueco. Bulkhead antes de la
    cuota: acota también a quien espera fichas. Rate limit dentro: lo más cerca del proveedor.
    Con `limiter` (Redis), la cuota RPM/TPM es la del despliegue, no la de cada réplica.
    """
    gateway = settings.llm_gateway
    quota: LLMPort = RateLimitedLLM(
        llm,
        requests_per_minute=gateway.requests_per_minute,
        tokens_per_minute=gateway.tokens_per_minute,
        max_wait=gateway.max_queue_seconds,
    )
    if limiter is not None:
        # Cuota del despliegue compartida entre réplicas; la local queda como red si Redis cae.
        quota = SharedQuotaLLM(
            quota,
            limiter,
            key=name,
            requests_per_minute=gateway.requests_per_minute,
            tokens_per_minute=gateway.tokens_per_minute,
            max_wait=gateway.max_queue_seconds,
        )
    return CircuitBreakerLLM(
        ConcurrencyLimitedLLM(
            ObservedLLM(
                quota,
                tracer=tracer,
                prices=prices,
                name=name,
                metadata=metadata,
            ),
            max_concurrency=gateway.max_concurrency,
            max_wait=gateway.max_queue_seconds,
        ),
        name=name,
        failure_threshold=gateway.circuit_failure_threshold,
        reset_timeout=gateway.circuit_reset_seconds,
    )


def _fallback(settings: Settings, candidates: list[LLMPort]) -> LLMPort:
    gateway = settings.llm_gateway
    return FallbackLLM(
        candidates,
        max_retries=gateway.max_retries,
        max_retry_after=gateway.max_retry_after_seconds,
    )


def _native_tier(
    settings: Settings,
    tier: ModelTier,
    tracer: LLMTracer,
    prices: PriceTable,
    limiter: RateLimiterPort | None,
) -> LLMPort:
    return _fallback(
        settings,
        [
            _resilient(
                settings,
                build_provider_llm(settings, provider, tier),
                tracer=tracer,
                prices=prices,
                name=f"{provider}:{tier}",
                metadata={"engine": "native", "provider": provider, "tier": tier},
                limiter=limiter,
            )
            for provider in settings.provider_chain()
        ],
    )


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


def build_judge_provider_llm(settings: Settings, provider: LLMProvider) -> LLMPort:
    """Modelo juez de evals de un proveedor (sin capas)."""
    if provider is LLMProvider.AZURE:
        from headache_core.adapters.azure_openai import AzureOpenAILLM

        azure = settings.azure_openai
        return AzureOpenAILLM.from_settings(azure, deployment=azure.judge_deployment)

    from headache_core.adapters.bedrock import BedrockConverseLLM

    return BedrockConverseLLM.from_settings(
        settings.bedrock, model_id=settings.bedrock.judge_model_id
    )


def shared_limiter(
    settings: Settings, limiter: RateLimiterPort | None = None
) -> RateLimiterPort | None:
    """El limitador compartido: el que se inyecta o, con `RATE_LIMIT__BACKEND=redis`, Redis."""
    if limiter is not None or settings.rate_limit.backend != "redis":
        return limiter
    from headache_core.adapters.redis_rate_limit import RedisRateLimiter

    return RedisRateLimiter.from_settings(
        settings.scale_redis, key_prefix=settings.rate_limit.key_prefix
    )


def build_judge_llm(
    settings: Settings,
    *,
    tracer: LLMTracer | None = None,
    limiter: RateLimiterPort | None = None,
) -> LLMPort:
    """Juez fijo, fuera del router: si lo enrutara, juzgaría el mismo modelo que genera."""
    tracer = tracer or build_tracer(settings)
    prices = PriceTable()
    return _fallback(
        settings,
        [
            _resilient(
                settings,
                build_judge_provider_llm(settings, provider),
                tracer=tracer,
                prices=prices,
                name=f"{provider}:judge",
                metadata={"engine": "native", "provider": provider, "tier": "judge"},
                limiter=shared_limiter(settings, limiter),
            )
            for provider in settings.provider_chain()
        ],
    )


def build_gateway(
    settings: Settings,
    *,
    tracer: LLMTracer | None = None,
    limiter: RateLimiterPort | None = None,
) -> LLMPort:
    """Gateway completo: routing barato/potente + fallback + breaker + bulkhead + rate limit +
    trazas y coste. Con `litellm`, los cooldowns y el rate limit son los del Router de LiteLLM."""
    tracer = tracer or build_tracer(settings)
    prices = PriceTable()
    routes: dict[ModelTier, LLMPort]
    if settings.llm_gateway.engine == "litellm":
        routes = _litellm_routes(settings, tracer, prices)
    else:
        shared = shared_limiter(settings, limiter)
        routes = {tier: _native_tier(settings, tier, tracer, prices, shared) for tier in ModelTier}
    classifier = (
        LLMClassifier(routes[ModelTier.CHEAP])
        if settings.llm_gateway.router == "llm"
        else HeuristicClassifier()
    )
    return RoutingLLM(routes, classifier)

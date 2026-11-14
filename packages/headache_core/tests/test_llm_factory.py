from __future__ import annotations

import pytest
from pydantic import SecretStr

from headache_core.adapters.azure_openai import AzureOpenAILLM
from headache_core.adapters.gemini import GeminiLLM
from headache_core.adapters.llm_factory import (
    build_gateway,
    build_judge_llm,
    build_judge_provider_llm,
    build_provider_llm,
    build_tracer,
)
from headache_core.application.llm import FallbackLLM, ModelTier, NullTracer, RoutingLLM
from headache_core.config import (
    AzureOpenAISettings,
    GcpSettings,
    LangfuseSettings,
    LLMGatewaySettings,
    LLMProvider,
    Settings,
)
from headache_core.ports import LLMError


@pytest.mark.parametrize("engine", ["native", "litellm"])
def test_gateway_wires_up_without_network(engine: str) -> None:
    settings = Settings(
        _env_file=None,
        llm_provider=LLMProvider.AZURE,
        azure_openai=AzureOpenAISettings(
            endpoint="https://x.openai.azure.com", api_key=SecretStr("k")
        ),
        llm_gateway=LLMGatewaySettings.model_validate(
            {"engine": engine, "fallback_providers": "aws"}
        ),
    )

    assert isinstance(build_gateway(settings, tracer=NullTracer()), RoutingLLM)


def test_judge_is_pinned_to_its_own_deployment() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider=LLMProvider.AZURE,
        azure_openai=AzureOpenAISettings(
            endpoint="https://x.openai.azure.com", api_key=SecretStr("k")
        ),
    )

    azure = settings.azure_openai

    assert isinstance(build_judge_provider_llm(settings, LLMProvider.AZURE), AzureOpenAILLM)
    assert azure.judge_deployment not in {azure.chat_deployment, azure.cheap_chat_deployment}
    assert isinstance(build_judge_llm(settings, tracer=NullTracer()), FallbackLLM)


def _three_clouds(engine: str) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider=LLMProvider.GCP,
        gcp=GcpSettings(project="headache-dev"),
        azure_openai=AzureOpenAISettings(
            endpoint="https://x.openai.azure.com", api_key=SecretStr("k")
        ),
        llm_gateway=LLMGatewaySettings.model_validate(
            {"engine": engine, "fallback_providers": "azure,aws"}
        ),
    )


def test_gcp_builds_gemini_for_each_tier() -> None:
    settings = _three_clouds("native")

    for tier in ModelTier:
        assert isinstance(build_provider_llm(settings, LLMProvider.GCP, tier), GeminiLLM)


@pytest.mark.parametrize("engine", ["native", "litellm"])
def test_gateway_wires_up_the_three_clouds(engine: str) -> None:
    settings = _three_clouds(engine)

    assert settings.provider_chain() == [LLMProvider.GCP, LLMProvider.AZURE, LLMProvider.AWS]
    assert isinstance(build_gateway(settings, tracer=NullTracer()), RoutingLLM)


def test_gcp_fails_loudly_where_its_adapter_does_not_exist_yet() -> None:
    """El riesgo no es el error, es el silencio: sin este corte, `gcp` construiría Bedrock."""
    with pytest.raises(LLMError, match=r"5\.G1"):
        build_judge_provider_llm(_three_clouds("native"), LLMProvider.GCP)


def test_without_langfuse_keys_nothing_is_traced() -> None:
    settings = Settings(_env_file=None, langfuse=LangfuseSettings(public_key=""))

    assert isinstance(build_tracer(settings), NullTracer)

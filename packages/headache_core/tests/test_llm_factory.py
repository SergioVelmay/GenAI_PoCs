from __future__ import annotations

import pytest
from pydantic import SecretStr

from headache_core.adapters.azure_openai import AzureOpenAILLM
from headache_core.adapters.llm_factory import (
    build_gateway,
    build_judge_llm,
    build_judge_provider_llm,
    build_tracer,
)
from headache_core.application.llm import FallbackLLM, NullTracer, RoutingLLM
from headache_core.config import (
    AzureOpenAISettings,
    LangfuseSettings,
    LLMGatewaySettings,
    LLMProvider,
    Settings,
)


@pytest.mark.parametrize("engine", ["native", "litellm"])
def test_gateway_wires_up_without_network(engine: str) -> None:
    settings = Settings(
        _env_file=None,
        llm_provider=LLMProvider.AZURE,
        azure_openai=AzureOpenAISettings(
            endpoint="https://x.openai.azure.com", api_key=SecretStr("k")
        ),
        llm_gateway=LLMGatewaySettings.model_validate(
            {"engine": engine, "fallback_provider": "aws"}
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


def test_without_langfuse_keys_nothing_is_traced() -> None:
    settings = Settings(_env_file=None, langfuse=LangfuseSettings(public_key=""))

    assert isinstance(build_tracer(settings), NullTracer)

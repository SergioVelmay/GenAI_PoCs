from __future__ import annotations

import pytest
from pydantic import SecretStr

from genaipocs_core.adapters.azure_openai import AzureOpenAILLM
from genaipocs_core.adapters.gemini import GeminiLLM
from genaipocs_core.adapters.llm_factory import (
    build_gateway,
    build_judge_llm,
    build_judge_provider_llm,
    build_provider_llm,
    build_tracer,
)
from genaipocs_core.application.llm import FallbackLLM, ModelTier, NullTracer, RoutingLLM
from genaipocs_core.config import (
    AzureOpenAISettings,
    GcpSettings,
    LangfuseSettings,
    LLMGatewaySettings,
    LLMProvider,
    Settings,
)
from genaipocs_core.ports import LLMError


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
        gcp=GcpSettings(project="genaipocs-dev"),
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


def test_gcp_judge_is_gemini_pinned_to_its_own_model_and_thinking() -> None:
    settings = _three_clouds("native")

    judge = build_judge_provider_llm(settings, LLMProvider.GCP)

    assert isinstance(judge, GeminiLLM)
    assert judge._model == settings.gemini.judge_model == "gemini-4-argon"
    assert judge._thinking_level == settings.gemini.judge_thinking_level == "low"
    # Con GCP de principal y Azure y AWS de respaldo, el juez también cae entre nubes.
    assert isinstance(build_judge_llm(settings, tracer=NullTracer()), FallbackLLM)


def test_online_agent_judge_is_built_on_gemini_without_azure() -> None:
    """12.G1: el juez online de los especialistas (12.3) con solo GCP configurado. Apagado con
    `sample_rate = 0`, como en cualquier nube."""
    from genaipocs_core.adapters.online_eval_factory import build_agent_evaluator
    from genaipocs_core.application.agent_online_eval import AgentOnlineEvaluator

    settings = Settings(
        _env_file=None,
        llm_provider=LLMProvider.GCP,
        gcp=GcpSettings(project="genaipocs-dev"),
        llm_gateway=LLMGatewaySettings.model_validate({"fallback_providers": ""}),
    )

    assert settings.provider_chain() == [LLMProvider.GCP]
    evaluator = build_agent_evaluator(settings, sample_rate=0.2, max_per_minute=10)
    assert isinstance(evaluator, AgentOnlineEvaluator)
    assert build_agent_evaluator(settings, sample_rate=0, max_per_minute=10) is None


@pytest.mark.parametrize(
    ("provider", "overrides"),
    [
        (LLMProvider.GCP, {"gemini": {"judge_model": "gemini-4-hydrogen"}}),
        (LLMProvider.AZURE, {"azure_openai": {"judge_deployment": "gpt-4.1-mini"}}),
        (LLMProvider.AWS, {"bedrock": {"judge_model_id": "eu.amazon.nova-lite-v1:0"}}),
    ],
)
def test_a_judge_that_is_also_a_router_model_is_rejected(
    provider: LLMProvider, overrides: dict[str, dict[str, str]]
) -> None:
    """Una variable de entorno puede poner de juez al generador: el juez se puntuaría a sí mismo."""
    base = _three_clouds("native").model_dump()
    for section, values in overrides.items():
        base[section] |= values
    settings = Settings.model_validate(base | {"llm_provider": provider})

    with pytest.raises(LLMError, match="router"):
        build_judge_provider_llm(settings, provider)


def test_a_provider_without_judge_fails_loudly() -> None:
    """El riesgo no es el error, es el silencio: sin este corte, `simulated` construiría Bedrock."""
    with pytest.raises(LLMError, match="no tiene modelo juez"):
        build_judge_provider_llm(_three_clouds("native"), LLMProvider.SIMULATED)


def test_without_langfuse_keys_nothing_is_traced() -> None:
    settings = Settings(_env_file=None, langfuse=LangfuseSettings(public_key=""))

    assert isinstance(build_tracer(settings), NullTracer)

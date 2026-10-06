from __future__ import annotations

from pydantic import SecretStr

from headache_core.adapters.litellm_gateway import LiteLLMGateway, build_router_config
from headache_core.config import (
    AzureOpenAISettings,
    BedrockSettings,
    LLMGatewaySettings,
    LLMProvider,
    Settings,
)
from headache_core.ports import ChatMessage, Role


def _settings(fallback: LLMProvider | None) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider=LLMProvider.AZURE,
        azure_openai=AzureOpenAISettings(
            endpoint="https://x.openai.azure.com", api_key=SecretStr("k")
        ),
        llm_gateway=LLMGatewaySettings(fallback_provider=fallback),
        bedrock=BedrockSettings(
            chat_model_id="eu.anthropic.claude-haiku-4-5-20251001-v1:0",
            cheap_chat_model_id="eu.amazon.nova-lite-v1:0",
        ),
    )


def test_router_config_has_one_group_per_provider_and_tier() -> None:
    model_list, fallbacks = build_router_config(_settings(LLMProvider.AWS))

    groups = {m["model_name"]: m["litellm_params"]["model"] for m in model_list}
    assert groups == {
        "azure-cheap": "azure/gpt-4.1-nano",
        "azure-strong": "azure/gpt-4.1-mini",
        "aws-cheap": "bedrock/converse/eu.amazon.nova-lite-v1:0",
        "aws-strong": "bedrock/converse/eu.anthropic.claude-haiku-4-5-20251001-v1:0",
    }
    assert fallbacks == [{"azure-cheap": ["aws-cheap"]}, {"azure-strong": ["aws-strong"]}]


def test_router_config_without_fallback() -> None:
    model_list, fallbacks = build_router_config(_settings(None))

    assert len(model_list) == 2
    assert fallbacks == []


async def test_gateway_falls_back_when_primary_is_rate_limited() -> None:
    from litellm.router import Router

    azure = {"api_key": "k", "api_base": "https://x.openai.azure.com", "api_version": "2024-10-21"}
    router = Router(
        model_list=[
            {
                "model_name": "primary",
                "litellm_params": {
                    "model": "azure/gpt-4.1-nano",
                    "mock_response": "litellm.RateLimitError",
                    **azure,
                },
            },
            {
                "model_name": "backup",
                "litellm_params": {"model": "azure/gpt-4.1-mini", "mock_response": "Hola", **azure},
            },
        ],
        fallbacks=[{"primary": ["backup"]}],
        num_retries=0,
    )

    response = await LiteLLMGateway(router, "primary").complete(
        [ChatMessage(Role.SYSTEM, "Manual", cache_checkpoint=True), ChatMessage(Role.USER, "hola")]
    )

    assert response.content == "Hola"
    assert response.provider.startswith("litellm:")

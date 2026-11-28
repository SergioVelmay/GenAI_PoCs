"""Router gestionado de cada nube (pasada 2.P): precios, regla de dominio, adaptadores y factoría.

Todo con dobles: Stubber de botocore para Bedrock, `FakeModels` para Gemini y `FakeLLM`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from botocore.stub import Stubber
from google.genai import types
from pydantic import SecretStr

from genaipocs_core.adapters.azure_openai import AzureOpenAILLM
from genaipocs_core.adapters.bedrock import BedrockConverseLLM
from genaipocs_core.adapters.gemini import GeminiLLM
from genaipocs_core.adapters.llm_factory import build_gateway, build_managed_router_llm
from genaipocs_core.adapters.memory import FakeLLM, RecordingTracer
from genaipocs_core.application.llm import (
    ManagedRoutingLLM,
    NullTracer,
    ObservedLLM,
    PriceTable,
)
from genaipocs_core.config import (
    AzureOpenAISettings,
    BedrockSettings,
    GcpSettings,
    LLMGatewaySettings,
    LLMProvider,
    Settings,
)
from genaipocs_core.ports import (
    ChatMessage,
    LLMError,
    LLMResponse,
    Role,
    TextDelta,
    TokenUsage,
    ToolConfig,
    ToolSpec,
)

if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime import BedrockRuntimeClient

HELLO = [ChatMessage(Role.USER, "¿A qué hora abren las puertas?")]
ROUTER_ARN = "arn:aws:bedrock:eu-west-1:111122223333:default-prompt-router/amazon.nova:1"
NOVA_PRO = "arn:aws:bedrock:eu-west-1:111122223333:inference-profile/eu.amazon.nova-pro-v1:0"
READ = ToolConfig((ToolSpec("get_order", "Estado de un pedido.", {"type": "object"}),))
WRITE = ToolConfig(
    (ToolSpec("request_refund", "Pide un reembolso.", {"type": "object"}, read_only=False),)
)


# --- Precios -------------------------------------------------------------------------------


def test_azure_router_fee_is_per_input_token_on_top_of_the_chosen_model() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=0)

    cost = PriceTable().routed_cost("azure-model-router", "gpt-4.1-nano-2025-04-14", usage)

    # 0,10 $ de gpt-4.1-nano + 0,154 $ del router (Data Zone).
    assert cost == Decimal("0.254")


def test_bedrock_router_fee_is_per_request_and_can_cost_more_than_the_model() -> None:
    prices = PriceTable()
    usage = TokenUsage(input_tokens=500, output_tokens=100)

    model_only = prices.cost("eu.amazon.nova-lite-v1:0", usage)
    routed = prices.routed_cost("bedrock-prompt-router", "eu.amazon.nova-lite-v1:0", usage)

    assert model_only == Decimal("0.000054")
    # Un turno corto: la tarifa del router (0,001 $) es ~18 veces el modelo barato.
    assert routed == model_only + Decimal("0.001")


def test_an_unknown_router_or_model_has_no_cost_instead_of_a_wrong_one() -> None:
    usage = TokenUsage(input_tokens=10, output_tokens=10)

    assert PriceTable().routed_cost("otro-router", "gpt-4.1-nano", usage) is None
    assert PriceTable().routed_cost("azure-model-router", "modelo-nuevo", usage) is None


def test_the_invoked_inference_profile_arn_resolves_to_its_price() -> None:
    assert PriceTable().lookup(NOVA_PRO) == PriceTable().lookup("amazon.nova-pro")


async def test_observed_router_adds_its_fee_to_the_traced_cost() -> None:
    tracer = RecordingTracer()
    llm = ObservedLLM(
        FakeLLM(["abren a las 19:00"], model="gpt-4.1-nano"),
        tracer=tracer,
        prices=PriceTable(),
        name="azure:router",
        router="bedrock-prompt-router",
    )

    await llm.complete(HELLO)

    [record] = tracer.records
    assert record.response is not None
    model_cost = PriceTable().cost("gpt-4.1-nano", record.response.usage)
    assert model_cost is not None
    assert record.cost_usd == model_cost + Decimal("0.001")


# --- Regla de dominio por delante del router ------------------------------------------------


@pytest.mark.parametrize(("tools", "expected"), [(None, "router"), (READ, "router")])
async def test_read_only_turns_go_to_the_managed_router(
    tools: ToolConfig | None, expected: str
) -> None:
    managed, strong = FakeLLM(["router"]), FakeLLM(["propio"])

    response = await ManagedRoutingLLM(managed, strong).complete(HELLO, tools=tools)

    assert response.content == expected
    assert strong.calls == []


async def test_a_turn_with_write_tools_never_reaches_the_managed_router() -> None:
    managed, strong = FakeLLM(["router"]), FakeLLM(["propio"])

    response = await ManagedRoutingLLM(managed, strong).complete(HELLO, tools=WRITE)

    assert response.content == "propio"
    assert managed.calls == []


async def test_streaming_goes_to_the_managed_router() -> None:
    llm = ManagedRoutingLLM(FakeLLM(["por el router"]), FakeLLM(["propio"]))

    events = [event async for event in llm.stream(HELLO)]

    assert "".join(e.text for e in events if isinstance(e, TextDelta)).strip() == "por el router"
    assert isinstance(events[-1], LLMResponse)


# --- Bedrock: el modelo elegido llega en el trace ------------------------------------------


def _bedrock() -> BedrockRuntimeClient:
    return boto3.client(
        "bedrock-runtime",
        region_name="eu-west-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def _converse(trace: dict[str, Any] | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "output": {"message": {"role": "assistant", "content": [{"text": "A las 19:00."}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 40, "outputTokens": 6, "totalTokens": 46},
        "metrics": {"latencyMs": 300},
    }
    if trace is not None:
        body["trace"] = trace
    return body


async def test_bedrock_reports_the_model_the_prompt_router_chose() -> None:
    client = _bedrock()
    stubber = Stubber(client)
    stubber.add_response(
        "converse",
        _converse({"promptRouter": {"invokedModelId": NOVA_PRO}}),
        {
            "modelId": ROUTER_ARN,
            "messages": [{"role": "user", "content": [{"text": HELLO[0].content}]}],
            "inferenceConfig": {"temperature": 0.0},
        },
    )

    with stubber:
        response = await BedrockConverseLLM(client, ROUTER_ARN).complete(HELLO)

    assert response.model == NOVA_PRO
    assert PriceTable().cost(response.model, response.usage) is not None


async def test_bedrock_without_router_keeps_the_requested_model() -> None:
    client = _bedrock()
    stubber = Stubber(client)
    stubber.add_response("converse", _converse(None))

    with stubber:
        response = await BedrockConverseLLM(client, "eu.amazon.nova-lite-v1:0").complete(HELLO)

    assert response.model == "eu.amazon.nova-lite-v1:0"


# --- Gemini: Model Optimizer -----------------------------------------------------------------


class FakeModels:
    """Doble de `Client.aio.models` con la versión de modelo que "elige" el router."""

    def __init__(self, model_version: str) -> None:
        self.model_version = model_version
        self.calls: list[dict[str, Any]] = []

    async def generate_content(
        self, *, model: str, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        self.calls.append({"model": model, "config": config})
        return types.GenerateContentResponse.model_validate(
            {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "19:00"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 3},
                "modelVersion": self.model_version,
            }
        )

    async def generate_content_stream(
        self, *, model: str, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> AsyncIterator[types.GenerateContentResponse]:
        raise NotImplementedError


async def test_gemini_router_sends_the_preference_and_reports_the_chosen_model() -> None:
    models = FakeModels("gemini-3.5-flash-lite")
    llm = GeminiLLM(models, "model-optimizer-exp-04-09", routing_preference="PRIORITIZE_COST")

    response = await llm.complete(HELLO)

    [call] = models.calls
    assert call["model"] == "model-optimizer-exp-04-09"
    config: types.GenerateContentConfig = call["config"]
    assert config.model_selection_config is not None
    assert (
        config.model_selection_config.feature_selection_preference
        is types.FeatureSelectionPreference.PRIORITIZE_COST
    )
    # El razonamiento lo decide el router según el modelo que elige.
    assert config.thinking_config is None
    assert response.model == "gemini-3.5-flash-lite"


def test_gemini_router_rejects_a_fixed_thinking_level() -> None:
    with pytest.raises(LLMError, match="Model Optimizer"):
        GeminiLLM(
            FakeModels("x"),
            "model-optimizer-exp-04-09",
            thinking_level="low",
            routing_preference="BALANCED",
        )


# --- Factoría -------------------------------------------------------------------------------


def _managed(provider: LLMProvider, *, engine: str = "native", arn: str = ROUTER_ARN) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider=provider,
        gcp=GcpSettings(project="genaipocs-dev"),
        azure_openai=AzureOpenAISettings(
            endpoint="https://x.openai.azure.com", api_key=SecretStr("k")
        ),
        bedrock=BedrockSettings(prompt_router_arn=arn),
        llm_gateway=LLMGatewaySettings.model_validate(
            {"engine": engine, "router": "managed", "fallback_providers": "aws,gcp"}
        ),
    )


def test_each_cloud_builds_its_managed_router() -> None:
    settings = _managed(LLMProvider.AZURE)

    azure = build_managed_router_llm(settings, LLMProvider.AZURE)
    aws = build_managed_router_llm(settings, LLMProvider.AWS)
    gcp = build_managed_router_llm(settings, LLMProvider.GCP)

    assert isinstance(azure, AzureOpenAILLM)
    assert isinstance(aws, BedrockConverseLLM)
    assert isinstance(gcp, GeminiLLM)


def test_gateway_with_managed_router_chains_the_three_clouds() -> None:
    gateway = build_gateway(_managed(LLMProvider.AZURE), tracer=NullTracer())

    assert isinstance(gateway, ManagedRoutingLLM)


def test_aws_managed_router_needs_the_router_arn() -> None:
    with pytest.raises(LLMError, match="BEDROCK__PROMPT_ROUTER_ARN"):
        build_gateway(_managed(LLMProvider.AWS, arn=""), tracer=NullTracer())


def test_managed_router_is_not_combined_with_litellm() -> None:
    with pytest.raises(LLMError, match="ENGINE=native"):
        build_gateway(_managed(LLMProvider.AZURE, engine="litellm"), tracer=NullTracer())


async def test_simulated_gateway_answers_through_the_managed_router() -> None:
    tracer = RecordingTracer()
    settings = Settings(
        _env_file=None,
        llm_provider=LLMProvider.SIMULATED,
        llm_gateway=LLMGatewaySettings(router="managed"),
    )
    settings.simulated_llm.ttft_ms = 0
    settings.simulated_llm.tokens_per_second = 1_000_000

    response = await build_gateway(settings, tracer=tracer).complete(HELLO)

    assert response.content
    [record] = tracer.records
    assert record.name == "simulated:router"

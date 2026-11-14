from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from headache_core.adapters.litellm_gateway import (
    LiteLLMGateway,
    _from_litellm_call,
    _to_litellm,
    build_router,
    build_router_config,
)
from headache_core.config import (
    AzureOpenAISettings,
    BedrockSettings,
    GcpSettings,
    GeminiSettings,
    LLMGatewaySettings,
    LLMProvider,
    Settings,
)
from headache_core.ports import ChatMessage, LLMError, Role, ToolCall

SIGNATURE = base64.b64encode(b"\x01firma-opaca\xff").decode()


def _settings(*fallbacks: LLMProvider, gemini: GeminiSettings | None = None) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider=LLMProvider.AZURE,
        azure_openai=AzureOpenAISettings(
            endpoint="https://x.openai.azure.com", api_key=SecretStr("k")
        ),
        llm_gateway=LLMGatewaySettings(fallback_providers=fallbacks),
        bedrock=BedrockSettings(
            chat_model_id="eu.anthropic.claude-haiku-4-5-20251001-v1:0",
            cheap_chat_model_id="eu.amazon.nova-lite-v1:0",
        ),
        gcp=GcpSettings(project="headache-dev"),
        gemini=gemini or GeminiSettings(),
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
    model_list, fallbacks = build_router_config(_settings())

    assert len(model_list) == 2
    assert fallbacks == []


def test_router_config_chains_the_three_clouds() -> None:
    settings = _settings(LLMProvider.AWS, LLMProvider.GCP)

    model_list, fallbacks = build_router_config(settings)

    params = {m["model_name"]: m["litellm_params"] for m in model_list}
    assert params["gcp-strong"] == {
        "model": "vertex_ai/gemini-4-hydrogen",
        "vertex_project": "headache-dev",
        "vertex_location": "global",
        "reasoning_effort": "low",
        "rpm": 30,
        "tpm": 50_000,
    }
    assert params["gcp-cheap"]["model"] == "vertex_ai/gemini-3.5-flash-lite"
    assert fallbacks == [
        {"azure-cheap": ["aws-cheap", "gcp-cheap"]},
        {"azure-strong": ["aws-strong", "gcp-strong"]},
    ]
    # Se construye sin red ni credenciales (ADC se resuelve en la primera llamada).
    assert build_router(settings) is not None


def test_gemini_developer_api_needs_a_key_and_agent_platform_a_project() -> None:
    developer = GeminiSettings(enterprise=False, api_key=SecretStr("k"), thinking_level="")
    model_list, _ = build_router_config(_settings(LLMProvider.GCP, gemini=developer))

    strong = next(m["litellm_params"] for m in model_list if m["model_name"] == "gcp-strong")
    assert (strong["model"], strong["api_key"]) == ("gemini/gemini-4-hydrogen", "k")
    assert "reasoning_effort" not in strong  # Vacío = el razonamiento por defecto del modelo.

    with pytest.raises(LLMError, match="GEMINI__API_KEY"):
        build_router_config(_settings(LLMProvider.GCP, gemini=GeminiSettings(enterprise=False)))
    no_project = _settings(LLMProvider.GCP).model_copy(update={"gcp": GcpSettings()})
    with pytest.raises(LLMError, match="GCP__PROJECT"):
        build_router_config(no_project)


def test_gemini_signature_leaves_the_id_and_returns_where_litellm_looks_for_it() -> None:
    """LiteLLM incrusta la firma en el id (`<id>__thought__<firma>`); el puerto la separa."""
    returned = SimpleNamespace(
        id=f"c1__thought__{SIGNATURE}",
        function=SimpleNamespace(name="get_order", arguments='{"id": "1"}'),
    )

    call = _from_litellm_call(returned)

    assert call == ToolCall("c1", "get_order", '{"id": "1"}', signature=SIGNATURE)
    # De vuelta, la conversión de LiteLLM a Gemini encuentra la firma en su sitio. Se importa
    # aquí, después del adaptador: importar `litellm` antes de que este fije LITELLM_MODE vuelca
    # el `.env` en `os.environ` de todo pytest (ya pasó: rompió tests de otros paquetes).
    from litellm.litellm_core_utils.prompt_templates.factory import (
        convert_to_gemini_tool_call_invoke,
    )

    message = _to_litellm(ChatMessage(Role.ASSISTANT, "", tool_calls=(call,)))
    parts = convert_to_gemini_tool_call_invoke(message, model="gemini-4-hydrogen")  # type: ignore[arg-type]
    assert parts[0]["thoughtSignature"] == SIGNATURE


def test_calls_without_signature_do_not_carry_provider_fields() -> None:
    """Hacia Azure o Bedrock no viaja nada que su esquema estricto pueda rechazar."""
    message = _to_litellm(
        ChatMessage(Role.ASSISTANT, "", tool_calls=(ToolCall("call_1", "get_order", "{}"),))
    )

    assert "provider_specific_fields" not in message["tool_calls"][0]


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


def test_importing_the_adapter_does_not_load_dotenv(tmp_path: Path) -> None:
    # Regresion: en modo DEV, `import litellm` vuelca el .env en os.environ de todo el proceso.
    (tmp_path / ".env").write_text("HEADACHE_DOTENV_PROBE=leaked\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("LITELLM_")}
    code = (
        "import os, headache_core.adapters.litellm_gateway; "
        "print(os.environ.get('HEADACHE_DOTENV_PROBE'))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "None"

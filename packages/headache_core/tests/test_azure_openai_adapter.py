from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from openai import AsyncAzureOpenAI

from headache_core.adapters.azure_openai import AzureOpenAILLM
from headache_core.ports import (
    ChatMessage,
    LLMError,
    LLMRateLimitError,
    LLMTransientError,
    ResponseSchema,
    Role,
    TokenUsage,
)

COMPLETION: dict[str, Any] = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4.1-mini-2025-04-14",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": '{"ok": true}', "refusal": None},
        }
    ],
    "usage": {
        "prompt_tokens": 2000,
        "completion_tokens": 10,
        "total_tokens": 2010,
        "prompt_tokens_details": {"cached_tokens": 1536},
    },
}


def _llm(handler: Callable[[httpx.Request], httpx.Response]) -> AzureOpenAILLM:
    client = AsyncAzureOpenAI(
        azure_endpoint="https://headache-test.openai.azure.com",
        api_key="test",
        api_version="2024-10-21",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return AzureOpenAILLM(client, "gpt-4.1-mini")


async def test_sends_strict_schema_and_maps_cached_tokens() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=COMPLETION)

    response = await _llm(handler).complete(
        [
            ChatMessage(Role.SYSTEM, "Manual de políticas", cache_checkpoint=True),
            ChatMessage(Role.USER, "¿Puedo cambiar la fecha?"),
        ],
        response_schema=ResponseSchema("Answer", {"type": "object"}),
    )

    assert "/openai/deployments/gpt-4.1-mini/chat/completions" in seen["url"]
    assert seen["body"]["response_format"]["json_schema"]["strict"] is True
    assert "max_tokens" not in seen["body"]
    # La caché de Azure es automática: el checkpoint no viaja en la petición.
    assert seen["body"]["messages"][0] == {"role": "system", "content": "Manual de políticas"}
    assert response.usage == TokenUsage(2000, 10, cached_input_tokens=1536)
    assert response.model == "gpt-4.1-mini-2025-04-14"
    assert response.provider == "azure"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, LLMRateLimitError), (503, LLMTransientError), (400, LLMError)],
)
async def test_maps_http_errors(status: int, expected: type[LLMError]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "boom", "code": "x"}})

    with pytest.raises(LLMError) as info:
        await _llm(handler).complete([ChatMessage(Role.USER, "hola")])
    assert type(info.value) is expected

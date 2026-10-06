from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from botocore.stub import Stubber

from headache_core.adapters.bedrock import BedrockConverseLLM
from headache_core.ports import (
    ChatMessage,
    LLMError,
    LLMRateLimitError,
    LLMTransientError,
    ResponseSchema,
    Role,
    TokenUsage,
)

if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime import BedrockRuntimeClient

MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
SCHEMA = ResponseSchema(
    "Answer", {"type": "object", "properties": {"ok": {"type": "boolean"}}}, "Respuesta"
)


def _response(content: list[dict[str, Any]], **usage: int) -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 50, "outputTokens": 5, "totalTokens": 55, **usage},
        "metrics": {"latencyMs": 300},
    }


def _client() -> BedrockRuntimeClient:
    return boto3.client(
        "bedrock-runtime",
        region_name="eu-west-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


async def test_inserts_cache_point_and_normalizes_usage() -> None:
    client = _client()
    stubber = Stubber(client)
    stubber.add_response(
        "converse",
        _response([{"text": "Sí, hasta 24 h antes."}], cacheReadInputTokens=4000),
        {
            "modelId": MODEL,
            "system": [{"text": "Manual de políticas"}, {"cachePoint": {"type": "default"}}],
            "messages": [{"role": "user", "content": [{"text": "¿Puedo cambiar la fecha?"}]}],
            "inferenceConfig": {"temperature": 0.0, "maxTokens": 100},
        },
    )

    with stubber:
        response = await BedrockConverseLLM(client, MODEL).complete(
            [
                ChatMessage(Role.SYSTEM, "Manual de políticas", cache_checkpoint=True),
                ChatMessage(Role.USER, "¿Puedo cambiar la fecha?"),
            ],
            max_tokens=100,
        )

    assert response.content == "Sí, hasta 24 h antes."
    assert response.usage == TokenUsage(4050, 5, cached_input_tokens=4000)
    assert response.provider == "aws"
    stubber.assert_no_pending_responses()


async def test_structured_output_via_forced_tool_use() -> None:
    client = _client()
    stubber = Stubber(client)
    tool_use = {"toolUse": {"toolUseId": "t1", "name": "Answer", "input": {"ok": True}}}
    stubber.add_response(
        "converse",
        _response([tool_use]),
        {
            "modelId": MODEL,
            "messages": [{"role": "user", "content": [{"text": "hola"}]}],
            "inferenceConfig": {"temperature": 0.0},
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": "Answer",
                            "description": "Respuesta",
                            "inputSchema": {"json": dict(SCHEMA.json_schema)},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": "Answer"}},
            },
        },
    )

    with stubber:
        response = await BedrockConverseLLM(client, MODEL).complete(
            [ChatMessage(Role.USER, "hola")], response_schema=SCHEMA
        )

    assert json.loads(response.content) == {"ok": True}


async def test_structured_output_native_mode() -> None:
    client = _client()
    stubber = Stubber(client)
    stubber.add_response(
        "converse",
        _response([{"text": '{"ok": true}'}]),
        {
            "modelId": MODEL,
            "messages": [{"role": "user", "content": [{"text": "hola"}]}],
            "inferenceConfig": {"temperature": 0.0},
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "name": "Answer",
                            "schema": json.dumps(dict(SCHEMA.json_schema)),
                        }
                    },
                }
            },
        },
    )

    with stubber:
        response = await BedrockConverseLLM(client, MODEL, structured_mode="native").complete(
            [ChatMessage(Role.USER, "hola")], response_schema=SCHEMA
        )

    assert json.loads(response.content) == {"ok": True}


@pytest.mark.parametrize(
    ("code", "status", "expected"),
    [
        ("ThrottlingException", 429, LLMRateLimitError),
        ("ServiceUnavailableException", 503, LLMTransientError),
        ("ValidationException", 400, LLMError),
    ],
)
async def test_maps_client_errors(code: str, status: int, expected: type[LLMError]) -> None:
    client = _client()
    stubber = Stubber(client)
    stubber.add_client_error("converse", service_error_code=code, http_status_code=status)

    with stubber, pytest.raises(LLMError) as info:
        await BedrockConverseLLM(client, MODEL).complete([ChatMessage(Role.USER, "hola")])
    assert type(info.value) is expected

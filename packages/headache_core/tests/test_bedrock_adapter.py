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
    ToolCall,
    ToolConfig,
    ToolSpec,
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


TOOLS = ToolConfig(
    (ToolSpec("get_order", "Estado de un pedido.", {"type": "object"}),), choice="required"
)


async def test_tool_use_round_trip_groups_parallel_results_in_one_user_turn() -> None:
    client = _client()
    stubber = Stubber(client)
    stubber.add_response(
        "converse",
        {
            **_response(
                [
                    {"text": "Consulto el otro pedido."},
                    {"toolUse": {"toolUseId": "t3", "name": "get_order", "input": {"id": "3"}}},
                ]
            ),
            "stopReason": "tool_use",
        },
        {
            "modelId": MODEL,
            "system": [{"text": "Asistente"}],
            "messages": [
                {"role": "user", "content": [{"text": "Mis pedidos 1 y 2"}]},
                {
                    "role": "assistant",
                    "content": [
                        {"toolUse": {"toolUseId": "t1", "name": "get_order", "input": {"id": "1"}}},
                        {"toolUse": {"toolUseId": "t2", "name": "get_order", "input": {}}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "toolResult": {
                                "toolUseId": "t1",
                                "content": [{"text": '{"status":"paid"}'}],
                                "status": "success",
                            }
                        },
                        {
                            "toolResult": {
                                "toolUseId": "t2",
                                "content": [{"text": '{"error":{}}'}],
                                "status": "error",
                            }
                        },
                    ],
                },
            ],
            "inferenceConfig": {"temperature": 0.0},
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": "get_order",
                            "description": "Estado de un pedido.",
                            "inputSchema": {"json": {"type": "object"}},
                        }
                    }
                ],
                "toolChoice": {"any": {}},
            },
        },
    )

    with stubber:
        response = await BedrockConverseLLM(client, MODEL).complete(
            [
                ChatMessage(Role.SYSTEM, "Asistente"),
                ChatMessage(Role.USER, "Mis pedidos 1 y 2"),
                ChatMessage(
                    Role.ASSISTANT,
                    "",
                    tool_calls=(
                        ToolCall("t1", "get_order", '{"id": "1"}'),
                        # Argumentos inválidos (p. ej. tras un fallback): viajan como objeto vacío.
                        ToolCall("t2", "get_order", "{roto"),
                    ),
                ),
                ChatMessage(Role.TOOL, '{"status":"paid"}', tool_call_id="t1"),
                ChatMessage(Role.TOOL, '{"error":{}}', tool_call_id="t2", is_error=True),
            ],
            tools=TOOLS,
        )

    assert response.content == "Consulto el otro pedido."
    assert response.tool_calls == (ToolCall("t3", "get_order", '{"id": "3"}'),)
    assert response.finish_reason == "tool_use"
    stubber.assert_no_pending_responses()


async def test_tool_mode_rejects_tools_with_forced_tool_structured_output() -> None:
    with pytest.raises(LLMError):
        await BedrockConverseLLM(_client(), MODEL).complete(
            [ChatMessage(Role.USER, "hola")], response_schema=SCHEMA, tools=TOOLS
        )

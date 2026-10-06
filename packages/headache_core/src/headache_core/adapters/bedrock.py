"""Adaptador Amazon Bedrock con la Converse API (misma forma para Claude, Nova, Llama...).

Prompt caching: explícito. Se inserta un bloque `cachePoint` al final del prefijo estable;
Bedrock informa de `cacheWriteInputTokens` (1.ª vez) y `cacheReadInputTokens` (siguientes).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, Self

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    ReadTimeoutError,
)
from botocore.exceptions import ConnectionError as BotoConnectionError

from headache_core.config import BedrockSettings
from headache_core.ports import (
    ChatMessage,
    LLMError,
    LLMRateLimitError,
    LLMResponse,
    LLMTransientError,
    ResponseSchema,
    Role,
    TokenUsage,
)

if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime import BedrockRuntimeClient
    from mypy_boto3_bedrock_runtime.type_defs import (
        ContentBlockTypeDef,
        ConverseRequestTypeDef,
        ConverseResponseTypeDef,
        MessageTypeDef,
        SystemContentBlockTypeDef,
    )

# "tool": tool use forzado (portable, funciona en Claude y Nova).
# "native": `outputConfig` con JSON Schema (más reciente; comprueba qué modelos lo soportan).
type StructuredMode = Literal["tool", "native"]

_TRANSIENT_CODES = frozenset(
    {
        "ServiceUnavailableException",
        "InternalServerException",
        "ModelNotReadyException",
        "ModelTimeoutException",
    }
)


def _to_llm_error(exc: ClientError | BotoCoreError) -> LLMError:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ThrottlingException":
            return LLMRateLimitError(str(exc))
        if code in _TRANSIENT_CODES:
            return LLMTransientError(str(exc))
        return LLMError(str(exc))
    if isinstance(exc, BotoConnectionError | ConnectTimeoutError | ReadTimeoutError):
        return LLMTransientError(str(exc))
    return LLMError(str(exc))  # Credenciales, región... no se arreglan reintentando.


class BedrockConverseLLM:
    def __init__(
        self,
        client: BedrockRuntimeClient,
        model_id: str,
        *,
        structured_mode: StructuredMode = "tool",
    ) -> None:
        self._client = client
        self._model_id = model_id
        self._structured_mode = structured_mode

    @classmethod
    def from_settings(
        cls,
        settings: BedrockSettings,
        *,
        model_id: str,
        structured_mode: StructuredMode = "tool",
    ) -> Self:
        session = boto3.Session(profile_name=settings.profile or None, region_name=settings.region)
        # Sin reintentos de botocore: los reintentos y el fallback son del gateway.
        client = session.client(
            "bedrock-runtime",
            config=Config(retries={"max_attempts": 1, "mode": "standard"}, read_timeout=120),
        )
        return cls(client, model_id, structured_mode=structured_mode)

    def _build_request(
        self,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int | None,
        response_schema: ResponseSchema | None,
    ) -> ConverseRequestTypeDef:
        system: list[SystemContentBlockTypeDef] = []
        conversation: list[MessageTypeDef] = []
        for message in messages:
            if message.role is Role.SYSTEM:
                system.append({"text": message.content})
                if message.cache_checkpoint:
                    system.append({"cachePoint": {"type": "default"}})
                continue
            content: list[ContentBlockTypeDef] = [{"text": message.content}]
            if message.cache_checkpoint:
                content.append({"cachePoint": {"type": "default"}})
            role: Literal["user", "assistant"] = (
                "user" if message.role is Role.USER else "assistant"
            )
            conversation.append({"role": role, "content": content})

        request: ConverseRequestTypeDef = {
            "modelId": self._model_id,
            "messages": conversation,
            "inferenceConfig": {"temperature": temperature},
        }
        if max_tokens is not None:
            request["inferenceConfig"]["maxTokens"] = max_tokens
        if system:
            request["system"] = system
        if response_schema is not None:
            if self._structured_mode == "native":
                request["outputConfig"] = {
                    "textFormat": {
                        "type": "json_schema",
                        "structure": {
                            "jsonSchema": {
                                "name": response_schema.name,
                                "schema": json.dumps(response_schema.json_schema),
                            }
                        },
                    }
                }
            else:
                request["toolConfig"] = {
                    "tools": [
                        {
                            "toolSpec": {
                                "name": response_schema.name,
                                "description": response_schema.description
                                or f"Devuelve la respuesta como {response_schema.name}.",
                                "inputSchema": {"json": dict(response_schema.json_schema)},
                            }
                        }
                    ],
                    "toolChoice": {"tool": {"name": response_schema.name}},
                }
        return request

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
    ) -> LLMResponse:
        request = self._build_request(messages, temperature, max_tokens, response_schema)
        started = time.perf_counter()
        try:
            # boto3 es síncrono: se ejecuta en un hilo para no bloquear el event loop.
            response = await asyncio.to_thread(lambda: self._client.converse(**request))
        except (ClientError, BotoCoreError) as exc:
            raise _to_llm_error(exc) from exc
        latency_ms = (time.perf_counter() - started) * 1000
        return LLMResponse(
            content=_extract_content(response),
            model=self._model_id,
            usage=_extract_usage(response),
            provider="aws",
            latency_ms=latency_ms,
            finish_reason=response["stopReason"],
        )


def _extract_content(response: ConverseResponseTypeDef) -> str:
    message = response["output"].get("message")
    blocks = message["content"] if message else []
    for block in blocks:
        if "toolUse" in block:
            return json.dumps(block["toolUse"]["input"], ensure_ascii=False)
    return "".join(block.get("text", "") for block in blocks)


def _extract_usage(response: ConverseResponseTypeDef) -> TokenUsage:
    usage = response["usage"]
    read = usage.get("cacheReadInputTokens", 0)
    write = usage.get("cacheWriteInputTokens", 0)
    # Bedrock excluye de inputTokens lo leído/escrito en caché; lo sumamos (semántica OpenAI).
    return TokenUsage(
        input_tokens=usage["inputTokens"] + read + write,
        output_tokens=usage["outputTokens"],
        cached_input_tokens=read,
        cache_write_tokens=write,
    )

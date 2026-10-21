"""`LLMTracer` sobre el puerto de telemetría: cada llamada al modelo es un span `chat {modelo}`.

Sustituye al SDK de Langfuse como camino principal: el span lleva la convención GenAI de OTel
(tokens, modelo, proveedor) más `langfuse.observation.type=generation` y el coste, así que el
mismo span sirve a Jaeger, a App Insights, a X-Ray y a Langfuse (el collector lo reparte).

El contenido de los mensajes **no** se guarda por defecto (`capture_content=False`): es la
parte con más PII y la que más pesa. Si se activa, pasa antes por el redactor.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from headache_core.application.telemetry import genai
from headache_core.application.telemetry.redaction import Redactor, redact_text
from headache_core.application.telemetry.runtime import get_telemetry
from headache_core.ports.llm import ChatMessage, LLMResponse, ToolCall
from headache_core.ports.telemetry import AttributeValue, SpanKind, SpanPort, TelemetryPort

_METADATA = {
    "engine": genai.HEADACHE_ENGINE,
    "tier": genai.HEADACHE_TIER,
    "stream": genai.HEADACHE_STREAM,
    "response_schema": genai.HEADACHE_RESPONSE_SCHEMA,
    "tools": genai.HEADACHE_TOOLS_OFFERED,
}


def _parts(content: str, tool_calls: Sequence[ToolCall], redact: Redactor) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if content:
        parts.append({"type": "text", "content": redact(content)})
    parts += [
        {"type": "tool_call", "id": c.id, "name": c.name, "arguments": redact(c.arguments)}
        for c in tool_calls
    ]
    return parts


def _input_messages(messages: Sequence[ChatMessage], redact: Redactor) -> str:
    payload: list[dict[str, Any]] = []
    for message in messages:
        if message.tool_call_id is not None:
            parts: list[dict[str, Any]] = [
                {
                    "type": "tool_call_response",
                    "id": message.tool_call_id,
                    "response": redact(message.content),
                }
            ]
        else:
            parts = _parts(message.content, message.tool_calls, redact)
        payload.append({"role": message.role.value, "parts": parts})
    return json.dumps(payload, ensure_ascii=False)


def _output_messages(response: LLMResponse, redact: Redactor) -> str:
    return json.dumps(
        [
            {
                "role": "assistant",
                "parts": _parts(response.content, response.tool_calls, redact),
                "finish_reason": response.finish_reason or "stop",
            }
        ],
        ensure_ascii=False,
    )


class _SpanGeneration:
    def __init__(self, span: SpanPort, *, capture_content: bool, redact: Redactor) -> None:
        self._span = span
        self._capture_content = capture_content
        self._redact = redact

    def finish(self, response: LLMResponse, *, cost_usd: Decimal | None) -> None:
        span = self._span
        span.update_name(genai.span_name(genai.GenAIOperation.CHAT, response.model))
        attributes: dict[str, AttributeValue] = {
            genai.PROVIDER_NAME: genai.provider_name(response.provider),
            genai.REQUEST_MODEL: response.model,
            genai.RESPONSE_MODEL: response.model,
            **genai.usage_attributes(response.usage),
        }
        if response.finish_reason:
            attributes[genai.RESPONSE_FINISH_REASONS] = [response.finish_reason]
        if cost_usd is not None:
            attributes[genai.USAGE_COST] = float(cost_usd)
        if self._capture_content:
            output = _output_messages(response, self._redact)
            attributes[genai.OUTPUT_MESSAGES] = output
            attributes[genai.LANGFUSE_OUTPUT] = output
        span.set_attributes(attributes)
        span.end()

    def fail(self, error: BaseException) -> None:
        self._span.set_attribute(genai.ERROR_TYPE, type(error).__name__)
        self._span.record_error(error)
        self._span.end()


class TelemetryLLMTracer:
    """Traza las *generations* con el puerto de telemetría (por defecto, el global)."""

    def __init__(
        self,
        telemetry: TelemetryPort | None = None,
        *,
        capture_content: bool = False,
        redact: Redactor = redact_text,
    ) -> None:
        self._telemetry = telemetry
        self._capture_content = capture_content
        self._redact = redact

    def start_generation(
        self,
        *,
        name: str,
        messages: Sequence[ChatMessage],
        metadata: Mapping[str, str],
    ) -> _SpanGeneration:
        attributes: dict[str, AttributeValue] = {
            genai.OPERATION_NAME: genai.GenAIOperation.CHAT.value,
            genai.LANGFUSE_OBSERVATION_TYPE: genai.ObservationType.GENERATION.value,
            genai.HEADACHE_DEPLOYMENT: name,
        }
        if "provider" in metadata:
            attributes[genai.PROVIDER_NAME] = genai.provider_name(metadata["provider"])
        attributes |= {_METADATA[k]: v for k, v in metadata.items() if k in _METADATA}
        if self._capture_content:
            prompt = _input_messages(messages, self._redact)
            attributes[genai.INPUT_MESSAGES] = prompt
            attributes[genai.LANGFUSE_INPUT] = prompt
        # La telemetría se resuelve en cada llamada: el gateway se construye a veces antes de
        # que el servicio haya configurado la global.
        telemetry = self._telemetry or get_telemetry()
        span = telemetry.start_span(
            genai.span_name(genai.GenAIOperation.CHAT, name),
            kind=SpanKind.CLIENT,
            attributes=attributes,
        )
        return _SpanGeneration(span, capture_content=self._capture_content, redact=self._redact)

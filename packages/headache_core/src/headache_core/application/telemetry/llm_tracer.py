"""`LLMTracer` sobre el puerto de telemetría: cada llamada al modelo es un span `chat {modelo}`.

Sustituye al SDK de Langfuse como camino principal: el span lleva la convención GenAI de OTel
(tokens, modelo, proveedor) más `langfuse.observation.type=generation` y el coste, así que el
mismo span sirve a Jaeger, a App Insights, a X-Ray y a Langfuse (el collector lo reparte).

El contenido de los mensajes solo se guarda si la telemetría lo permite (`content()`, con
`TELEMETRY__CAPTURE_CONTENT=true`), y redactado: es la parte con más PII y la que más pesa.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from headache_core.application.telemetry import genai, metrics
from headache_core.application.telemetry.runtime import current_agent, get_telemetry
from headache_core.ports.llm import ChatMessage, LLMResponse, ToolCall
from headache_core.ports.telemetry import AttributeValue, SpanKind, SpanPort, TelemetryPort

_METADATA = {
    "engine": genai.HEADACHE_ENGINE,
    "tier": genai.HEADACHE_TIER,
    "stream": genai.HEADACHE_STREAM,
    "response_schema": genai.HEADACHE_RESPONSE_SCHEMA,
    "tools": genai.HEADACHE_TOOLS_OFFERED,
}


def _parts(content: str, tool_calls: Sequence[ToolCall]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if content:
        parts.append({"type": "text", "content": content})
    parts += [
        {"type": "tool_call", "id": c.id, "name": c.name, "arguments": c.arguments}
        for c in tool_calls
    ]
    return parts


def _input_messages(messages: Sequence[ChatMessage]) -> str:
    payload: list[dict[str, Any]] = []
    for message in messages:
        if message.tool_call_id is not None:
            parts: list[dict[str, Any]] = [
                {
                    "type": "tool_call_response",
                    "id": message.tool_call_id,
                    "response": message.content,
                }
            ]
        else:
            parts = _parts(message.content, message.tool_calls)
        payload.append({"role": message.role.value, "parts": parts})
    return json.dumps(payload, ensure_ascii=False)


def _output_messages(response: LLMResponse) -> str:
    return json.dumps(
        [
            {
                "role": "assistant",
                "parts": _parts(response.content, response.tool_calls),
                "finish_reason": response.finish_reason or "stop",
            }
        ],
        ensure_ascii=False,
    )


class _SpanGeneration:
    def __init__(
        self, span: SpanPort, telemetry: TelemetryPort, metric_attributes: dict[str, AttributeValue]
    ) -> None:
        self._span = span
        self._telemetry = telemetry
        self._metric_attributes = metric_attributes
        self._started = time.perf_counter()

    def first_token(self) -> None:
        # TTFT: Langfuse lo calcula con este atributo; en Jaeger se ve como evento.
        now = datetime.now(UTC).isoformat()
        self._span.set_attribute(genai.LANGFUSE_COMPLETION_START_TIME, now)
        self._span.add_event(genai.EVENT_FIRST_TOKEN)
        metrics.llm_first_token(time.perf_counter() - self._started, self._metric_attributes)

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
        output = self._telemetry.content(_output_messages(response))
        if output is not None:
            attributes[genai.OUTPUT_MESSAGES] = output
            attributes[genai.LANGFUSE_OUTPUT] = output
        span.set_attributes(attributes)
        span.end()
        metrics.llm_call(
            seconds=time.perf_counter() - self._started,
            attributes={**self._metric_attributes, genai.RESPONSE_MODEL: response.model},
            usage=response.usage,
            cost_usd=cost_usd,
        )

    def fail(self, error: BaseException) -> None:
        self._span.set_attribute(genai.ERROR_TYPE, type(error).__name__)
        self._span.record_error(error)
        self._span.end()
        # `error.type` distingue 429 del proveedor (LLMRateLimitError), cuota del cliente
        # (LLMOverloadedError), presupuesto agotado y cortes del usuario (CancelledError).
        metrics.llm_call(
            seconds=time.perf_counter() - self._started,
            attributes={**self._metric_attributes, genai.ERROR_TYPE: type(error).__name__},
        )


class TelemetryLLMTracer:
    """Traza las *generations* con el puerto de telemetría (por defecto, el global)."""

    def __init__(self, telemetry: TelemetryPort | None = None) -> None:
        self._telemetry = telemetry

    def start_generation(
        self,
        *,
        name: str,
        messages: Sequence[ChatMessage],
        metadata: Mapping[str, str],
    ) -> _SpanGeneration:
        # Se resuelve en cada llamada: el gateway se construye a veces antes de que el servicio
        # haya configurado la telemetría global.
        telemetry = self._telemetry or get_telemetry()
        attributes: dict[str, AttributeValue] = {
            genai.OPERATION_NAME: genai.GenAIOperation.CHAT.value,
            genai.LANGFUSE_OBSERVATION_TYPE: genai.ObservationType.GENERATION.value,
            genai.HEADACHE_DEPLOYMENT: name,
        }
        if "provider" in metadata:
            attributes[genai.PROVIDER_NAME] = genai.provider_name(metadata["provider"])
        attributes |= {_METADATA[k]: v for k, v in metadata.items() if k in _METADATA}
        prompt = telemetry.content(_input_messages(messages))
        if prompt is not None:
            attributes[genai.INPUT_MESSAGES] = prompt
            attributes[genai.LANGFUSE_INPUT] = prompt
        span = telemetry.start_span(
            genai.span_name(genai.GenAIOperation.CHAT, name),
            kind=SpanKind.CLIENT,
            attributes=attributes,
        )
        metric_attributes: dict[str, AttributeValue] = {
            genai.OPERATION_NAME: genai.GenAIOperation.CHAT.value,
            genai.HEADACHE_DEPLOYMENT: name,
            genai.HEADACHE_TIER: metadata.get("tier", "unknown"),
            genai.AGENT_NAME: current_agent() or metrics.NO_AGENT,
        }
        if genai.PROVIDER_NAME in attributes:
            metric_attributes[genai.PROVIDER_NAME] = attributes[genai.PROVIDER_NAME]
        return _SpanGeneration(span, telemetry, metric_attributes)

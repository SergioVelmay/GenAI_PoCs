"""Trazas en Langfuse (SDK v4, basado en OpenTelemetry). Cada llamada es una *generation*."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Self

from langfuse import Langfuse

from headache_core.config import LangfuseSettings
from headache_core.ports import ChatMessage, LLMResponse, ToolCall


def _calls(calls: Sequence[ToolCall]) -> list[dict[str, str]]:
    return [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls]


def _message(message: ChatMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = _calls(message.tool_calls)
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
        payload["is_error"] = message.is_error
    return payload


class _LangfuseGeneration:
    def __init__(self, observation: Any) -> None:
        self._observation = observation

    def finish(self, response: LLMResponse, *, cost_usd: Decimal | None) -> None:
        usage = response.usage
        # Langfuse suma todas las claves para el total: `input` va sin lo cacheado.
        uncached = usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens
        self._observation.update(
            model=response.model,
            output=(
                {"content": response.content, "tool_calls": _calls(response.tool_calls)}
                if response.tool_calls
                else response.content
            ),
            usage_details={
                "input": max(uncached, 0),
                "output": usage.output_tokens,
                "cache_read_input_tokens": usage.cached_input_tokens,
                "cache_creation_input_tokens": usage.cache_write_tokens,
            },
            cost_details=None if cost_usd is None else {"total": float(cost_usd)},
            metadata={
                "provider": response.provider,
                "latency_ms": round(response.latency_ms, 1),
                "finish_reason": response.finish_reason,
            },
        )
        self._observation.end()

    def fail(self, error: BaseException) -> None:
        self._observation.update(level="ERROR", status_message=f"{type(error).__name__}: {error}")
        self._observation.end()


class LangfuseTracer:
    def __init__(self, client: Langfuse) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: LangfuseSettings) -> Self:
        return cls(
            Langfuse(
                public_key=settings.public_key,
                secret_key=settings.secret_key.get_secret_value(),
                host=settings.host,
            )
        )

    @property
    def client(self) -> Langfuse:
        return self._client

    def start_generation(
        self,
        *,
        name: str,
        messages: Sequence[ChatMessage],
        metadata: Mapping[str, str],
    ) -> _LangfuseGeneration:
        observation = self._client.start_observation(
            name=name,
            as_type="generation",
            input=[_message(m) for m in messages],
            metadata=dict(metadata),
        )
        return _LangfuseGeneration(observation)

    def flush(self) -> None:
        self._client.flush()

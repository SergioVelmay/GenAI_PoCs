"""Puerto de modelos de lenguaje: Azure OpenAI, Bedrock y LiteLLM lo implementan igual."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str
    # Fin de un prefijo estable que conviene cachear. Bedrock lo traduce a `cachePoint`;
    # Azure OpenAI lo ignora porque cachea solo (prefijos idénticos de >= 1024 tokens).
    cache_checkpoint: bool = False


@dataclass(frozen=True, slots=True)
class ResponseSchema:
    """JSON Schema que la respuesta debe cumplir (structured outputs)."""

    name: str
    json_schema: Mapping[str, Any]
    description: str = ""


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """`input_tokens` incluye siempre los leídos y escritos en caché (semántica de OpenAI).

    Bedrock los cuenta aparte; su adaptador los suma para que el coste se calcule igual.
    """

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    model: str
    usage: TokenUsage
    provider: str = "unknown"
    latency_ms: float = 0.0
    finish_reason: str | None = None


class LLMError(Exception):
    """Fallo no recuperable (petición inválida, credenciales, filtro de contenido...)."""


class LLMTransientError(LLMError):
    """Fallo pasajero (timeout, 5xx): se puede reintentar o saltar a otro proveedor."""


class LLMRateLimitError(LLMTransientError):
    """Cuota agotada (HTTP 429 / ThrottlingException)."""


class LLMPort(Protocol):
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
    ) -> LLMResponse: ...


class GenerationObservation(Protocol):
    """Una llamada al modelo en curso dentro de la traza (una *generation* en Langfuse)."""

    def finish(self, response: LLMResponse, *, cost_usd: Decimal | None) -> None: ...

    def fail(self, error: BaseException) -> None: ...


class LLMTracer(Protocol):
    def start_generation(
        self,
        *,
        name: str,
        messages: Sequence[ChatMessage],
        metadata: Mapping[str, str],
    ) -> GenerationObservation: ...

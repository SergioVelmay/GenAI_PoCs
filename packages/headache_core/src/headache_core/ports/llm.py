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
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Petición del modelo para ejecutar una tool. El backend decide si la ejecuta."""

    id: str
    name: str
    # JSON crudo tal como lo generó el modelo: puede ser inválido y se valida al ejecutar.
    arguments: str


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Contrato de una tool que ve el modelo: nombre, cuándo usarla y JSON Schema de entrada."""

    name: str
    description: str
    json_schema: Mapping[str, Any]
    strict: bool = True
    # No viaja al proveedor: lo usa el router (una tool con efectos exige el modelo potente).
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class ToolConfig:
    tools: tuple[ToolSpec, ...]
    # "auto" (el modelo decide), "required" (al menos una tool) o el nombre de una tool concreta.
    choice: str = "auto"
    # False obliga a una tool por turno (Azure). Bedrock no lo expone de forma portable.
    parallel: bool = True

    def __post_init__(self) -> None:
        if not self.tools:
            raise ValueError("ToolConfig necesita al menos una tool.")
        names = {tool.name for tool in self.tools}
        if len(names) != len(self.tools):
            raise ValueError("Nombres de tool duplicados.")
        if self.choice not in {"auto", "required"} | names:
            raise ValueError(f"tool choice desconocido: {self.choice!r}")


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str
    # Fin de un prefijo estable que conviene cachear. Bedrock lo traduce a `cachePoint`;
    # Azure OpenAI lo ignora porque cachea solo (prefijos idénticos de >= 1024 tokens).
    cache_checkpoint: bool = False
    # Solo en ASSISTANT: tools que el modelo pidió en ese turno.
    tool_calls: tuple[ToolCall, ...] = ()
    # Solo en TOOL: a qué llamada responde y si es un error (Bedrock lo marca como status=error).
    tool_call_id: str | None = None
    is_error: bool = False


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
    tool_calls: tuple[ToolCall, ...] = ()


class LLMError(Exception):
    """Fallo no recuperable (petición inválida, credenciales, filtro de contenido...)."""


class LLMContentFilterError(LLMError):
    """El filtro de contenido del proveedor bloqueó la petición (p. ej., Prompt Shields)."""


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
        tools: ToolConfig | None = None,
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

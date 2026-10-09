"""Model routing: cada petición va al modelo más barato capaz de resolverla."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from headache_core.application.llm.structured import complete_structured
from headache_core.ports import (
    ChatMessage,
    LLMError,
    LLMPort,
    LLMResponse,
    ResponseSchema,
    Role,
    ToolConfig,
)


class ModelTier(StrEnum):
    CHEAP = "cheap"
    STRONG = "strong"


class ComplexityClassifier(Protocol):
    async def classify(
        self, messages: Sequence[ChatMessage], *, response_schema: ResponseSchema | None
    ) -> ModelTier: ...


def _last_user_message(messages: Sequence[ChatMessage]) -> str:
    # Solo cuenta la petición: un system prompt largo y cacheado no la hace más difícil.
    return next((m.content for m in reversed(messages) if m.role is Role.USER), "")


DEFAULT_STRONG_HINTS = (
    "why",
    "explain",
    "compare",
    "analy",
    "step by step",
    "complaint",
    "lawyer",
    "por qué",
    "porqué",
    "explica",
    "compara",
    "analiza",
    "paso a paso",
    "queja",
    "reclamación",
    "abogado",
)


class HeuristicClassifier:
    """Gratis, determinista y con latencia ~0: longitud y palabras que indican razonamiento."""

    def __init__(
        self, *, max_cheap_chars: int = 1500, strong_hints: Sequence[str] = DEFAULT_STRONG_HINTS
    ) -> None:
        self._max_cheap_chars = max_cheap_chars
        self._hints = tuple(h.lower() for h in strong_hints)

    async def classify(
        self, messages: Sequence[ChatMessage], *, response_schema: ResponseSchema | None
    ) -> ModelTier:
        request = _last_user_message(messages)
        if len(request) > self._max_cheap_chars:
            return ModelTier.STRONG
        lowered = request.lower()
        return ModelTier.STRONG if any(h in lowered for h in self._hints) else ModelTier.CHEAP


class RoutingVerdict(BaseModel):
    """Decisión del clasificador de complejidad."""

    model_config = ConfigDict(extra="forbid")

    tier: ModelTier = Field(description="cheap: extracción/FAQ simple; strong: razonamiento")
    reason: str = Field(description="Justificación en menos de 15 palabras")


ROUTER_PROMPT = (
    "Eres el router de modelos de Headache. Clasifica la petición del usuario:\n"
    "- cheap: extracción de datos, clasificación, FAQ, respuestas cortas y factuales.\n"
    "- strong: razonamiento en varios pasos, quejas delicadas, comparaciones, redacción larga.\n"
    "Ante la duda, cheap."
)


class LLMClassifier:
    """Un modelo barato decide. Más preciso que la heurística, pero añade una llamada."""

    def __init__(self, llm: LLMPort, *, max_chars: int = 4000) -> None:
        self._llm = llm
        self._max_chars = max_chars

    async def classify(
        self, messages: Sequence[ChatMessage], *, response_schema: ResponseSchema | None
    ) -> ModelTier:
        request = _last_user_message(messages)[: self._max_chars]
        try:
            result = await complete_structured(
                self._llm,
                [ChatMessage(Role.SYSTEM, ROUTER_PROMPT), ChatMessage(Role.USER, request)],
                RoutingVerdict,
                max_tokens=60,
            )
        except LLMError:
            return ModelTier.STRONG  # Si el router falla, prima la calidad.
        return result.value.tier


class RoutingLLM:
    def __init__(self, routes: Mapping[ModelTier, LLMPort], classifier: ComplexityClassifier):
        missing = set(ModelTier) - set(routes)
        if missing:
            raise ValueError(f"Faltan rutas para: {sorted(missing)}")
        self._routes = dict(routes)
        self._classifier = classifier

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        tier = await self._classifier.classify(messages, response_schema=response_schema)
        return await self._routes[tier].complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=response_schema,
            tools=tools,
        )

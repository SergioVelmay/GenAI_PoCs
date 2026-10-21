"""Degradación (fase 10.7): con el modelo saturado se responde con citas literales, no con 503."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from typing import Any

import pytest

from edge_api.assistant import Answer, AnswerChunk, RagAssistant, final_answer
from edge_api.cache import CachedAssistant
from headache_core.adapters.local_retrieval import LocalRetrieval
from headache_core.adapters.memory import HashingEmbedding, InMemorySemanticCache
from headache_core.application.answer_stream import StreamingAnswerer
from headache_core.application.retrieval import RetrievalService
from headache_core.application.semantic_cache import SemanticCache
from headache_core.domain.admission import Caller, ClientPlan
from headache_core.ports import (
    ChatMessage,
    LLMOverloadedError,
    LLMResponse,
    LLMStreamEvent,
    TextDelta,
    VectorRecord,
)

ANA = Caller("user:ana", ClientPlan.PUBLIC)
QUESTION = "¿Cuándo abren las puertas en Barcelona?"


class SaturatedLLM:
    """Falla con `LLMOverloadedError` tras `chunks` trozos (0 = antes de empezar)."""

    def __init__(self, chunks: int = 0) -> None:
        self.chunks = chunks

    async def complete(self, messages: Sequence[ChatMessage], **_: Any) -> LLMResponse:
        raise LLMOverloadedError("Sin cuota.", retry_after=3)

    async def stream(
        self, messages: Sequence[ChatMessage], **_: Any
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        for _chunk in range(self.chunks):
            yield TextDelta("Abren ")
        raise LLMOverloadedError("Sin cuota.", retry_after=3)


async def _assistant(llm: SaturatedLLM, *, degrade: bool = True) -> RagAssistant:
    embeddings = HashingEmbedding(64)
    text = "Las puertas abren 15 minutos antes del inicio en Barcelona."
    [vector] = await embeddings.embed_texts([text])
    record = VectorRecord(
        "faq-barcelona-es::chunk-0",
        text,
        vector,
        {"document_id": "faq-barcelona-es", "title": "FAQ", "acl": "public", "language": "es"},
    )
    service = RetrievalService(LocalRetrieval([record], embeddings=embeddings))
    return RagAssistant(
        service,
        mode="generative",
        answerer=StreamingAnswerer(service, llm),
        degrade_on_saturation=degrade,
    )


async def test_saturated_model_degrades_to_literal_citations() -> None:
    assistant = await _assistant(SaturatedLLM())

    events = [event async for event in assistant.stream(QUESTION, ANA)]

    final = events[-1]
    assert isinstance(final, Answer)
    assert final.degraded and final.mode == "extractive"
    assert final.citations and not final.abstained
    assert "".join(e.text for e in events if isinstance(e, AnswerChunk)) == final.text
    assert assistant.outcomes["degraded"] == 1


async def test_cannot_switch_mode_once_the_answer_has_started() -> None:
    assistant = await _assistant(SaturatedLLM(chunks=2))

    with pytest.raises(LLMOverloadedError):
        await final_answer(assistant.stream(QUESTION, ANA))
    assert assistant.outcomes["degraded"] == 0


async def test_degradation_can_be_disabled() -> None:
    assistant = await _assistant(SaturatedLLM(), degrade=False)

    with pytest.raises(LLMOverloadedError):
        await assistant.answer(QUESTION, ANA)


async def test_degraded_answers_are_not_cached() -> None:
    cache = SemanticCache(InMemorySemanticCache(), HashingEmbedding(256), threshold=0.55)
    cached = CachedAssistant(
        await _assistant(SaturatedLLM()), cache, index_version="v1", mode="generative"
    )

    first = await cached.answer(QUESTION, ANA)
    second = await cached.answer(QUESTION, ANA)

    assert first.degraded and second.degraded
    assert second.cache == "miss"  # Se recalcula: cuando el modelo vuelva, responderá él.

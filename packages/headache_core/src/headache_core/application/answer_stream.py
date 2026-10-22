"""Respuesta RAG en streaming (fase 10.4): el usuario lee mientras el modelo escribe.

El precio del streaming es que la respuesta se valida **después** de enseñarla. Las citas `[n]`
se comprueban al final contra las fuentes recuperadas: si alguna no existe o no hay ninguna,
el evento final lo dice (`verified=False`), la interfaz lo marca y la respuesta no se cachea.
Lo que no se puede hacer es "des-enseñar" un texto: para lo que no admite error (importes,
reembolsos) no se hace streaming, se valida antes de responder (fase 8).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from headache_core.application.llm.streaming import stream_from
from headache_core.application.retrieval import RetrievalRequest, RetrievalService
from headache_core.ports import ChatMessage, LLMPort, LLMResponse, Role, TextDelta, TokenUsage
from headache_core.prompts import PROMPTS

ABSTENTION = "No hay evidencia suficiente en las fuentes autorizadas."

SYSTEM_PROMPT = PROMPTS.get("answer-stream").text

_CITATION = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True, slots=True)
class AnswerDelta:
    text: str


@dataclass(frozen=True, slots=True)
class StreamedAnswer:
    """Evento final: el texto completo y lo que se ha podido comprobar de él."""

    text: str
    citations: tuple[str, ...]
    abstained: bool
    mode: Literal["generative"]
    # Todas las citas existen y hay al menos una (o es una abstención).
    verified: bool
    invalid_citations: tuple[int, ...] = ()
    usage: TokenUsage | None = None


type AnswerEvent = AnswerDelta | StreamedAnswer


def check_citations(text: str, sources: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Números citados válidos e inválidos, en orden de aparición y sin repetir."""
    cited = list(dict.fromkeys(int(n) for n in _CITATION.findall(text)))
    return (
        tuple(n for n in cited if 1 <= n <= sources),
        tuple(n for n in cited if not 1 <= n <= sources),
    )


class StreamingAnswerer:
    def __init__(self, retrieval: RetrievalService, llm: LLMPort, *, max_tokens: int = 400) -> None:
        self._retrieval = retrieval
        self._llm = llm
        self._max_tokens = max_tokens

    async def stream(self, request: RetrievalRequest) -> AsyncIterator[AnswerEvent]:
        results = (await self._retrieval.retrieve(request)).results
        if not results:
            # Sin fuentes no se llama al modelo: ni coste ni ocasión de inventar.
            yield AnswerDelta(ABSTENTION)
            yield StreamedAnswer(ABSTENTION, (), True, "generative", verified=True)
            return
        sources = "\n\n".join(
            f"[{i}] {result.record.text[:2000]}" for i, result in enumerate(results, 1)
        )
        messages = [
            ChatMessage(Role.SYSTEM, SYSTEM_PROMPT),
            ChatMessage(Role.USER, f"Fuentes:\n{sources}\n\nPregunta: {request.query}"),
        ]
        final: LLMResponse | None = None
        async for event in stream_from(self._llm, messages, max_tokens=self._max_tokens):
            if isinstance(event, TextDelta):
                yield AnswerDelta(event.text)
            else:
                final = event
        text = final.content if final else ""
        valid, invalid = check_citations(text, len(results))
        yield StreamedAnswer(
            text=text,
            citations=tuple(results[n - 1].record.id for n in valid),
            abstained=not valid,
            mode="generative",
            verified=bool(valid) and not invalid,
            invalid_citations=invalid,
            usage=final.usage if final else None,
        )

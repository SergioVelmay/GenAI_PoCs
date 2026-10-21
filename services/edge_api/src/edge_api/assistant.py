"""Lo que hay detrás del edge: el asistente de preguntas (RAG de la fase 4).

El edge solo conoce el contrato `Assistant`; los tests usan uno falso y en local se monta el
`RetrievalService` en proceso (sin saltos de red ni un servicio más que escalar).

Modos de respuesta (`EDGE__ANSWER_MODE`):
- `extractive`: citas literales, sin modelo (coste 0). Se "streamea" en trozos.
- `llm-extractive`: el modelo elige citas literales (salida estructurada, sin streaming real).
- `generative` (10.4): el modelo redacta con citas `[n]` en streaming; las citas se verifican
  al final y una respuesta sin verificar no se cachea.

Degradación (10.7): si el modelo está saturado (cuota, breaker abierto, sin hueco) antes de
empezar a responder, se contesta en modo extractivo (citas literales, coste 0) marcado como
`degraded`, en vez de un 503. Una vez empezado el stream ya no se puede cambiar de modo.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

from headache_core.application.answer_stream import AnswerDelta, StreamingAnswerer
from headache_core.application.retrieval import RetrievalRequest, RetrievalService
from headache_core.domain.admission import Caller, access_scope
from headache_core.ports import LLMTransientError

type AnswerMode = Literal["extractive", "llm-extractive", "generative"]


@dataclass(frozen=True, slots=True)
class Answer:
    text: str
    citations: tuple[str, ...]
    abstained: bool
    mode: AnswerMode
    # De dónde sale: calculada (`miss`), de la caché (`hit`), de otra petición idéntica en
    # curso (`coalesced`) o sin pasar por la caché por ser personal (`bypass`).
    cache: Literal["miss", "hit", "coalesced", "bypass"] = "miss"
    # Citas comprobadas contra las fuentes. En streaming se sabe al final (fase 10.4).
    verified: bool = True
    # Respuesta de emergencia (extractiva) porque el modelo estaba saturado: no se cachea.
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class AnswerChunk:
    text: str


# Un stream de respuesta: trozos de texto y, al final, la `Answer` completa.
type AssistantEvent = AnswerChunk | Answer


class Assistant(Protocol):
    async def answer(self, question: str, caller: Caller) -> Answer: ...

    def stream(self, question: str, caller: Caller) -> AsyncIterator[AssistantEvent]: ...


async def replay(answer: Answer) -> AsyncIterator[AssistantEvent]:
    """Una respuesta ya hecha (caché, extractiva) contada en trozos: el cliente la pinta igual."""
    for word in re.findall(r"\S+\s*", answer.text):
        yield AnswerChunk(word)
    yield answer


async def final_answer(events: AsyncIterator[AssistantEvent]) -> Answer:
    answer: Answer | None = None
    async for event in events:
        if isinstance(event, Answer):
            answer = event
    if answer is None:
        raise RuntimeError("El stream terminó sin respuesta final.")
    return answer


class RagAssistant:
    def __init__(
        self,
        service: RetrievalService,
        *,
        mode: AnswerMode = "extractive",
        answerer: StreamingAnswerer | None = None,
        degrade_on_saturation: bool = True,
    ) -> None:
        if mode == "generative" and answerer is None:
            raise ValueError("El modo generativo necesita un StreamingAnswerer (con modelo).")
        self._service = service
        self._mode = mode
        self._answerer = answerer
        self._degrade = degrade_on_saturation
        self.outcomes: Counter[str] = Counter()

    async def answer(self, question: str, caller: Caller) -> Answer:
        if self._mode == "generative":
            return await final_answer(self.stream(question, caller))
        request = RetrievalRequest(question, scope=access_scope(caller.plan))
        try:
            return await self._retrieve(request, generate=self._mode == "llm-extractive")
        except LLMTransientError:
            if not self._degrade or self._mode == "extractive":
                raise
            return await self._degraded(request)

    async def stream(self, question: str, caller: Caller) -> AsyncIterator[AssistantEvent]:
        if self._answerer is None or self._mode != "generative":
            async for event in replay(await self.answer(question, caller)):
                yield event
            return
        request = RetrievalRequest(question, scope=access_scope(caller.plan))
        started = False
        try:
            async for part in self._answerer.stream(request):
                started = True
                if isinstance(part, AnswerDelta):
                    yield AnswerChunk(part.text)
                else:
                    yield Answer(
                        part.text, part.citations, part.abstained, part.mode, verified=part.verified
                    )
        except LLMTransientError:
            if started or not self._degrade:
                raise
            fallback = await self._degraded(request)
        else:
            return
        async for event in replay(fallback):
            yield event

    async def _retrieve(self, request: RetrievalRequest, *, generate: bool) -> Answer:
        response = await self._service.answer(request, generate=generate)
        return Answer(
            response.answer,
            tuple(citation.source_id for citation in response.citations),
            response.abstained,
            response.mode,
        )

    async def _degraded(self, request: RetrievalRequest) -> Answer:
        self.outcomes["degraded"] += 1
        answer = await self._retrieve(request, generate=False)
        return Answer(answer.text, answer.citations, answer.abstained, answer.mode, degraded=True)

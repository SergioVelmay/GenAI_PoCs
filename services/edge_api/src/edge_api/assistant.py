"""Lo que hay detrás del edge: el asistente de preguntas (RAG de la fase 4).

El edge solo conoce el contrato `Assistant`; los tests usan uno falso y en local se monta el
`RetrievalService` en proceso (sin saltos de red ni un servicio más que escalar).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from headache_core.application.retrieval import RetrievalRequest, RetrievalService
from headache_core.domain.admission import Caller, access_scope


@dataclass(frozen=True, slots=True)
class Answer:
    text: str
    citations: tuple[str, ...]
    abstained: bool
    mode: Literal["extractive", "llm-extractive"]


class Assistant(Protocol):
    async def answer(self, question: str, caller: Caller) -> Answer: ...


class RagAssistant:
    def __init__(self, service: RetrievalService, *, generate: bool) -> None:
        self._service = service
        self._generate = generate

    async def answer(self, question: str, caller: Caller) -> Answer:
        request = RetrievalRequest(question, scope=access_scope(caller.plan))
        response = await self._service.answer(request, generate=self._generate)
        return Answer(
            response.answer,
            tuple(citation.source_id for citation in response.citations),
            response.abstained,
            response.mode,
        )

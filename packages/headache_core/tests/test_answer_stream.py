"""Respuesta RAG en streaming (fase 10.4): trozos según llegan y citas verificadas al final."""

from __future__ import annotations

import pytest

from headache_core.adapters.local_retrieval import LocalRetrieval
from headache_core.adapters.memory import FakeLLM, HashingEmbedding
from headache_core.application.answer_stream import (
    ABSTENTION,
    AnswerDelta,
    StreamedAnswer,
    StreamingAnswerer,
    check_citations,
)
from headache_core.application.retrieval import RetrievalRequest, RetrievalService
from headache_core.ports import VectorRecord

DOCS = [
    ("faq-barcelona-es", "Las puertas abren 15 minutos antes del inicio en Barcelona."),
    ("policy-theatre-es", "Las entradas de teatro no admiten reembolso."),
]


async def _answerer(llm: FakeLLM, docs: list[tuple[str, str]] = DOCS) -> StreamingAnswerer:
    embeddings = HashingEmbedding(64)
    vectors = await embeddings.embed_texts([text for _, text in docs])
    records = [
        VectorRecord(
            f"{doc_id}::chunk-0",
            text,
            vector,
            {"document_id": doc_id, "title": doc_id, "acl": "public", "language": "es"},
        )
        for (doc_id, text), vector in zip(docs, vectors, strict=True)
    ]
    return StreamingAnswerer(RetrievalService(LocalRetrieval(records, embeddings=embeddings)), llm)


async def _run(answerer: StreamingAnswerer, query: str) -> tuple[list[str], StreamedAnswer]:
    deltas, final = [], None
    async for event in answerer.stream(RetrievalRequest(query, top_k=2)):
        if isinstance(event, AnswerDelta):
            deltas.append(event.text)
        else:
            final = event
    assert final is not None
    return deltas, final


@pytest.mark.parametrize(
    ("text", "sources", "valid", "invalid"),
    [
        ("Abren 15 minutos antes [1].", 2, (1,), ()),
        ("Según [2] y [1], y otra vez [2].", 2, (2, 1), ()),
        ("Abren antes [3].", 2, (), (3,)),
        ("Sin citas.", 2, (), ()),
    ],
)
def test_citations_are_checked_against_the_sources(
    text: str, sources: int, valid: tuple[int, ...], invalid: tuple[int, ...]
) -> None:
    assert check_citations(text, sources) == (valid, invalid)


async def test_streams_deltas_and_maps_citations_to_sources() -> None:
    llm = FakeLLM(["Las puertas abren 15 minutos antes [1]."])

    deltas, final = await _run(await _answerer(llm), "¿Cuándo abren las puertas en Barcelona?")

    assert len(deltas) > 1
    assert "".join(deltas) == final.text
    assert final.verified and not final.abstained
    assert final.citations[0].startswith(("faq-barcelona-es", "policy-theatre-es"))
    assert final.usage is not None and final.usage.output_tokens > 0
    prompt = llm.calls[0][-1].content
    assert "[1]" in prompt and "Pregunta:" in prompt


async def test_invented_citation_is_flagged_after_streaming() -> None:
    llm = FakeLLM(["Abren 30 minutos antes [7]."])

    _, final = await _run(await _answerer(llm), "¿Cuándo abren las puertas?")

    assert not final.verified
    assert final.invalid_citations == (7,)


async def test_answer_without_citations_is_not_verified() -> None:
    _, final = await _run(await _answerer(FakeLLM(["Creo que a las ocho."])), "¿Horario?")

    assert (final.verified, final.abstained) == (False, True)


async def test_without_sources_the_model_is_not_called() -> None:
    llm = FakeLLM(["no debería llamarse"])

    deltas, final = await _run(await _answerer(llm, docs=[]), "¿Algo?")

    assert deltas == [ABSTENTION]
    assert final.abstained and final.verified
    assert llm.calls == []

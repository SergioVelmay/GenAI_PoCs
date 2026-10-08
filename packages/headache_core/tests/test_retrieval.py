"""Contratos deterministas de retrieval, sin red ni modelos."""

import pytest

from headache_core.adapters.local_retrieval import LocalRetrieval, TokenCoverageReranker
from headache_core.adapters.memory import FakeLLM, HashingEmbedding
from headache_core.application.retrieval import (
    InvalidCitationError,
    RetrievalRequest,
    RetrievalService,
    RetrievalUnavailableError,
    reciprocal_rank_fusion,
)
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.retrieval import RetrievalStrategy
from headache_core.ports.vector_store import SearchResult, VectorRecord


def hit(record_id: str, score: float = 1.0) -> SearchResult:
    return SearchResult(VectorRecord(record_id, record_id, []), score)


def test_rrf_uses_ranks_and_deduplicates() -> None:
    results = reciprocal_rank_fusion(
        [[hit("a", 1000), hit("b", 500), hit("b", 400)], [hit("b", 0.9), hit("c", 0.8)]]
    )
    assert [result.record.id for result in results] == ["b", "a", "c"]
    assert results[0].score == pytest.approx(1 / 62 + 1 / 61)


def test_rrf_empty_and_limit() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert len(reciprocal_rank_fusion([[hit("a"), hit("b")]], top_k=1)) == 1
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([], rank_constant=0)


@pytest.fixture
async def backend() -> LocalRetrieval:
    embeddings = HashingEmbedding(64)
    texts = [
        "Refund candlelit within 48 hours.",
        "Madrid venue accessible.",
        "Refund secret internal.",
    ]
    vectors = await embeddings.embed_texts(texts)
    records = [
        VectorRecord(
            "policy::0",
            texts[0],
            vectors[0],
            {
                "document_id": "policy",
                "title": "Refund policy",
                "language": "en",
                "doc_type": "policy",
                "acl": "public",
                "parent_id": "policy::parent",
            },
        ),
        VectorRecord(
            "venue::0",
            texts[1],
            vectors[1],
            {
                "document_id": "venue",
                "language": "en",
                "city": "Madrid",
                "acl": "public",
            },
        ),
        VectorRecord("secret::0", texts[2], vectors[2], {"acl": "internal"}),
    ]
    parent = VectorRecord(
        "policy::parent",
        texts[0] + " Booking fees excluded.",
        [],
        {
            **records[0].metadata,
        },
    )
    return LocalRetrieval(records, embeddings=embeddings, parents=[parent])


@pytest.mark.parametrize("strategy", ["bm25", "vector", "hybrid", "rerank", "parent-child"])
async def test_local_strategies_and_acl(
    backend: LocalRetrieval, strategy: RetrievalStrategy
) -> None:
    service = RetrievalService(backend, reranker=TokenCoverageReranker())
    response = await service.retrieve(RetrievalRequest("Refund", strategy=strategy))
    expected = "policy::parent" if strategy == "parent-child" else "policy::0"
    assert [result.record.id for result in response.results] == [expected]
    assert await service.source("secret::0") is None
    assert await service.source("secret::0", scope=AccessScope(("internal",))) is not None


async def test_filters_apply_before_top_k(backend: LocalRetrieval) -> None:
    response = await RetrievalService(backend).retrieve(
        RetrievalRequest("Madrid", top_k=1, filters={"city": "Madrid"})
    )
    assert [result.record.id for result in response.results] == ["venue::0"]
    assert not (
        await RetrievalService(backend).retrieve(
            RetrievalRequest("Refund", filters={"language": "es"})
        )
    ).results
    with pytest.raises(ValueError, match="ACL"):
        RetrievalRequest("Refund", filters={"acl": "internal"})


async def test_extractive_citations_are_verifiable(backend: LocalRetrieval) -> None:
    service = RetrievalService(backend)
    response = await service.answer(RetrievalRequest("Refund", strategy="bm25"))
    assert not response.abstained
    for citation in response.citations:
        source = await service.source(citation.source_id)
        assert source is not None and citation.quote in source.text
    missing = await service.answer(RetrievalRequest("zzzzunfindable", strategy="bm25"))
    assert missing.abstained and not missing.citations


@pytest.mark.parametrize("strategy", ["rewrite", "hyde", "subquestions", "router", "auto"])
async def test_llm_strategies_use_port(
    backend: LocalRetrieval, strategy: RetrievalStrategy
) -> None:
    llm = FakeLLM(['{"queries":["Refund"],"filters":{"doc_type":"policy"}}'])
    response = await RetrievalService(backend, llm=llm).retrieve(
        RetrievalRequest("Refund", strategy=strategy)
    )
    assert response.results[0].record.id == "policy::0"
    assert len(llm.calls) == 1 and llm.schemas[0] is not None
    with pytest.raises(RetrievalUnavailableError):
        await RetrievalService(backend).retrieve(RetrievalRequest("Refund", strategy=strategy))


async def test_auto_cannot_override_explicit_filters(backend: LocalRetrieval) -> None:
    llm = FakeLLM(['{"queries":["Refund"],"filters":{"language":"en"}}'])
    with pytest.raises(ValueError, match="reemplazar"):
        await RetrievalService(backend, llm=llm).retrieve(
            RetrievalRequest("Refund", strategy="auto", filters={"language": "es"})
        )


@pytest.mark.parametrize(
    "evidence",
    [
        '{"source_id":"secret::0","quote":"Refund secret internal."}',
        '{"source_id":"policy::0","quote":"Refund within 1 hour."}',
    ],
)
async def test_invented_citations_fail_closed(backend: LocalRetrieval, evidence: str) -> None:
    llm = FakeLLM(['{"evidence":[' + evidence + "]}"])
    with pytest.raises(InvalidCitationError):
        await RetrievalService(backend, llm=llm).answer(
            RetrievalRequest("Refund", strategy="bm25"), generate=True
        )


async def test_llm_only_selects_literal_evidence(backend: LocalRetrieval) -> None:
    llm = FakeLLM(['{"evidence":[{"source_id":"policy::0","quote":"within 48 hours."}]}'])
    response = await RetrievalService(backend, llm=llm).answer(
        RetrievalRequest("Refund", strategy="bm25"), generate=True
    )
    assert response.answer == "within 48 hours. [1]"
    assert response.mode == "llm-extractive"


def test_missing_acl_is_denied() -> None:
    assert not AccessScope().permits({"language": "es"})


async def test_subquestions_fuse_and_deduplicate(backend: LocalRetrieval) -> None:
    llm = FakeLLM(['{"queries":["Refund","Refund candlelit"],"filters":{}}'])
    response = await RetrievalService(backend, llm=llm).retrieve(
        RetrievalRequest("Refund", strategy="subquestions")
    )
    assert response.queries == ("Refund", "Refund candlelit")
    assert [result.record.id for result in response.results] == ["policy::0"]


async def test_plan_cannot_inject_acl(backend: LocalRetrieval) -> None:
    invalid = '{"queries":["Refund"],"filters":{"acl":"internal"}}'
    llm = FakeLLM([invalid, invalid])
    from headache_core.application.llm.structured import StructuredOutputError

    with pytest.raises(StructuredOutputError):
        await RetrievalService(backend, llm=llm).retrieve(
            RetrievalRequest("Refund", strategy="auto")
        )


async def test_empty_corpus_and_empty_tokens() -> None:
    backend = LocalRetrieval([], embeddings=HashingEmbedding(64))
    assert (
        await backend.search("Refund", mode="hybrid", top_k=5, filters={}, scope=AccessScope())
        == []
    )


async def test_parent_expansion_does_not_widen_acl() -> None:
    embeddings = HashingEmbedding(64)
    vector = await embeddings.embed_query("Refund")
    child = VectorRecord("child", "Refund", vector, {"acl": "public", "parent_id": "parent"})
    parent = VectorRecord("parent", "Refund secret", [], {"acl": "internal"})
    backend = LocalRetrieval([child], embeddings=embeddings, parents=[parent])
    response = await RetrievalService(backend).retrieve(
        RetrievalRequest("Refund", strategy="parent-child")
    )
    assert [result.record.id for result in response.results] == ["child"]

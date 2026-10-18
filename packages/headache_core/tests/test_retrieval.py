"""Contratos deterministas de retrieval, sin red ni modelos."""

import pytest

from headache_core.adapters.llamaindex_retrieval import LlamaIndexRetrieval
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
from headache_core.ports.retrieval import RetrievalPort, RetrievalStrategy
from headache_core.ports.vector_store import MetadataValue, SearchResult, VectorRecord


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


@pytest.fixture(params=["native", "llamaindex"])
async def backend(request: pytest.FixtureRequest) -> RetrievalPort:
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
    factory = LlamaIndexRetrieval if request.param == "llamaindex" else LocalRetrieval
    return factory(records, embeddings=embeddings, parents=[parent])


@pytest.mark.parametrize("strategy", ["bm25", "vector", "hybrid", "rerank", "parent-child"])
async def test_local_strategies_and_acl(
    backend: RetrievalPort, strategy: RetrievalStrategy
) -> None:
    service = RetrievalService(backend, reranker=TokenCoverageReranker())
    response = await service.retrieve(RetrievalRequest("Refund", strategy=strategy))
    expected = "policy::parent" if strategy == "parent-child" else "policy::0"
    assert [result.record.id for result in response.results] == [expected]
    assert await service.source("secret::0") is None
    assert await service.source("secret::0", scope=AccessScope(("internal",))) is not None


async def test_filters_apply_before_top_k(backend: RetrievalPort) -> None:
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


async def test_extractive_citations_are_verifiable(backend: RetrievalPort) -> None:
    service = RetrievalService(backend)
    response = await service.answer(RetrievalRequest("Refund", strategy="bm25"))
    assert not response.abstained
    for citation in response.citations:
        source = await service.source(citation.source_id)
        assert source is not None and citation.quote in source.text
    missing = await service.answer(RetrievalRequest("zzzzunfindable", strategy="bm25"))
    assert missing.abstained and not missing.citations


@pytest.mark.parametrize("strategy", ["rewrite", "hyde", "subquestions", "router", "auto"])
async def test_llm_strategies_use_port(backend: RetrievalPort, strategy: RetrievalStrategy) -> None:
    plan = (
        '{"queries":["Refund"],"filters":{"doc_type":"policy"}}'
        if strategy == "subquestions"
        else '{"query":"Refund","filters":{"doc_type":"policy"}}'
    )
    llm = FakeLLM([plan])
    response = await RetrievalService(backend, llm=llm).retrieve(
        RetrievalRequest("Refund", strategy=strategy)
    )
    assert response.results[0].record.id == "policy::0"
    assert len(llm.calls) == 1 and llm.schemas[0] is not None
    with pytest.raises(RetrievalUnavailableError):
        await RetrievalService(backend).retrieve(RetrievalRequest("Refund", strategy=strategy))


@pytest.mark.parametrize("strategy", ["rewrite", "hyde", "router", "auto"])
async def test_single_query_strategies_cannot_return_several_queries(
    strategy: RetrievalStrategy,
) -> None:
    # Regresión (Azure): con una lista, nano devolvía varias consultas y la pregunta se perdía.
    from headache_core.application.retrieval import QueryPlan, SingleQueryPlan

    llm = FakeLLM(['{"query":"Refund","filters":{}}'])
    backend = LocalRetrieval([], embeddings=HashingEmbedding(64))
    await RetrievalService(backend, llm=llm).retrieve(RetrievalRequest("Refund", strategy=strategy))
    schema = llm.schemas[0]
    assert schema is not None and schema.name == SingleQueryPlan.__name__
    assert "queries" not in schema.json_schema["properties"]
    assert QueryPlan.__name__ != schema.name


async def test_plan_filters_only_accept_domain_codes(backend: RetrievalPort) -> None:
    # Regresión (Azure): el modelo copiaba la etiqueta visible en vez del código del dominio.
    from headache_core.application.llm.structured import StructuredOutputError

    label = '{"query":"Refund","filters":{"category":"Gastronomía y bebidas"}}'
    with pytest.raises(StructuredOutputError):
        await RetrievalService(backend, llm=FakeLLM([label, label])).retrieve(
            RetrievalRequest("Refund", strategy="auto")
        )
    code = '{"query":"Refund","filters":{"category":"food_drink","doc_type":"policy"}}'
    response = await RetrievalService(backend, llm=FakeLLM([code])).retrieve(
        RetrievalRequest("Refund", strategy="auto")
    )
    assert response.filters == {"category": "food_drink", "doc_type": "policy"}
    assert all(type(value) is str for value in response.filters.values())


async def test_auto_cannot_override_explicit_filters(backend: RetrievalPort) -> None:
    llm = FakeLLM(['{"query":"Refund","filters":{"language":"en"}}'])
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
async def test_invented_citations_fail_closed(backend: RetrievalPort, evidence: str) -> None:
    llm = FakeLLM(['{"evidence":[' + evidence + "]}"])
    with pytest.raises(InvalidCitationError):
        await RetrievalService(backend, llm=llm).answer(
            RetrievalRequest("Refund", strategy="bm25"), generate=True
        )


async def test_llm_only_selects_literal_evidence(backend: RetrievalPort) -> None:
    llm = FakeLLM(['{"evidence":[{"source_id":"policy::0","quote":"within 48 hours."}]}'])
    response = await RetrievalService(backend, llm=llm).answer(
        RetrievalRequest("Refund", strategy="bm25"), generate=True
    )
    assert response.answer == "within 48 hours. [1]"
    assert response.mode == "llm-extractive"


def test_missing_acl_is_denied() -> None:
    assert not AccessScope().permits({"language": "es"})


async def test_subquestions_fuse_and_deduplicate(backend: RetrievalPort) -> None:
    llm = FakeLLM(['{"queries":["Refund","Refund candlelit"],"filters":{}}'])
    response = await RetrievalService(backend, llm=llm).retrieve(
        RetrievalRequest("Refund", strategy="subquestions")
    )
    assert response.queries == ("Refund", "Refund candlelit")
    assert [result.record.id for result in response.results] == ["policy::0"]


async def test_plan_cannot_inject_acl(backend: RetrievalPort) -> None:
    invalid = '{"query":"Refund","filters":{"acl":"internal"}}'
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


@pytest.mark.parametrize("candidate_k,expected", [(1, "child-1"), (2, "parent")])
async def test_auto_merging_depends_on_ratio(candidate_k: int, expected: str) -> None:
    embeddings = HashingEmbedding(64)
    texts = ["refund refund candlelit", "refund fees fees", "venue accessibility"]
    vectors = await embeddings.embed_texts(texts)
    metadata: dict[str, MetadataValue] = {
        "acl": "public",
        "document_id": "policy",
        "parent_id": "parent",
    }
    children = [
        VectorRecord(f"child-{index}", text, vector, metadata)
        for index, (text, vector) in enumerate(zip(texts, vectors, strict=True))
    ]
    parent = VectorRecord(
        "parent", "\n".join(texts), [], {"acl": "public", "document_id": "policy"}
    )
    backend = LlamaIndexRetrieval(children, embeddings=embeddings, parents=[parent])
    service = RetrievalService(backend)
    response = await service.retrieve(
        RetrievalRequest(
            "refund",
            strategy="auto-merge",
            top_k=1,
            candidate_k=candidate_k,
        )
    )
    assert [result.record.id for result in response.results] == [expected]
    answer = await service.answer(
        RetrievalRequest(
            "refund",
            strategy="auto-merge",
            top_k=1,
            candidate_k=candidate_k,
        )
    )
    source = await service.source(answer.citations[0].source_id)
    assert source is not None and answer.citations[0].quote in source.text
    sparse = await service.retrieve(
        RetrievalRequest(
            "candlelit",
            strategy="auto-merge",
            top_k=1,
            candidate_k=3,
        )
    )
    assert [result.record.id for result in sparse.results] == ["child-0"]


async def test_auto_merging_cannot_read_restricted_parent() -> None:
    embeddings = HashingEmbedding(64)
    child = VectorRecord(
        "child",
        "refund",
        await embeddings.embed_query("refund"),
        {
            "acl": "public",
            "parent_id": "parent",
        },
    )
    parent = VectorRecord("parent", "refund private", [], {"acl": "internal"})
    backend = LlamaIndexRetrieval([child], embeddings=embeddings, parents=[parent])
    response = await RetrievalService(backend).retrieve(
        RetrievalRequest("refund", strategy="auto-merge")
    )
    assert [result.record.id for result in response.results] == ["child"]


async def test_native_auto_merge_is_explicitly_unsupported(backend: RetrievalPort) -> None:
    if isinstance(backend, LocalRetrieval):
        with pytest.raises(RetrievalUnavailableError):
            await RetrievalService(backend).retrieve(
                RetrievalRequest("Refund", strategy="auto-merge")
            )


async def test_framework_never_uses_default_models(monkeypatch: pytest.MonkeyPatch) -> None:
    from llama_index.core import Settings

    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("No resolver modelos por defecto")

    monkeypatch.setattr(Settings, "_llm", None)
    monkeypatch.setattr(Settings, "_embed_model", None)
    monkeypatch.setattr("llama_index.core.settings.resolve_llm", blocked)
    monkeypatch.setattr("llama_index.core.settings.resolve_embed_model", blocked)
    embeddings = HashingEmbedding(64)
    record = VectorRecord(
        "source", "refund", await embeddings.embed_query("refund"), {"acl": "public"}
    )
    backend = LlamaIndexRetrieval([record], embeddings=embeddings)
    response = await RetrievalService(backend).retrieve(RetrievalRequest("refund"))
    assert response.results[0].record.id == "source"


@pytest.mark.parametrize("factory", [LocalRetrieval, LlamaIndexRetrieval])
async def test_bm25_normalizes_without_changing_sources(
    factory: type[LocalRetrieval] | type[LlamaIndexRetrieval],
) -> None:
    embeddings = HashingEmbedding(64)
    text = "Gesti\u00f3n del reembolso"
    record = VectorRecord("source", text, await embeddings.embed_query(text), {"acl": "public"})
    backend = factory([record], embeddings=embeddings)
    response = await RetrievalService(backend).answer(RetrievalRequest("GESTION", strategy="bm25"))
    assert not response.abstained
    assert response.citations[0].quote == text

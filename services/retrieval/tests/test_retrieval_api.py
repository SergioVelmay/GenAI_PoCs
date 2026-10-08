"""Contrato HTTP y arranque local, sin red ni coste cloud."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from headache_core.adapters.llamaindex_retrieval import LlamaIndexRetrieval
from headache_core.adapters.local_retrieval import LocalRetrieval, TokenCoverageReranker
from headache_core.adapters.memory import HashingEmbedding
from headache_core.application.retrieval import RetrievalRequest, RetrievalService
from headache_core.ports.vector_store import VectorRecord
from retrieval.app import STRATEGIES, create_app
from retrieval.bootstrap import access_metadata, build_local_runtime, load_synthetic
from retrieval.compare import compare
from retrieval.settings import RetrievalSettings


@pytest.fixture(params=["native", "llamaindex"])
async def client(request: pytest.FixtureRequest) -> AsyncIterator[AsyncClient]:
    embeddings = HashingEmbedding(32)
    texts = ["Reembolso hasta 48 horas antes.", "Reembolso secreto de operaciones."]
    vectors = await embeddings.embed_texts(texts)
    records = [
        VectorRecord(
            "policy::0000",
            texts[0],
            vectors[0],
            {
                "acl": "public",
                "language": "es",
                "document_id": "policy",
                "title": "Reembolsos",
            },
        ),
        VectorRecord("secret::0000", texts[1], vectors[1], {"acl": "internal"}),
    ]
    factory = LlamaIndexRetrieval if request.param == "llamaindex" else LocalRetrieval
    service = RetrievalService(
        factory(records, embeddings=embeddings), reranker=TokenCoverageReranker()
    )
    settings = RetrievalSettings(_env_file=None, engine=request.param, source="synthetic")
    async with AsyncClient(
        transport=ASGITransport(app=create_app(service, settings)), base_url="http://test"
    ) as session:
        yield session


@pytest.mark.parametrize("strategy", ["bm25", "vector", "hybrid", "rerank", "parent-child"])
async def test_local_endpoints(client: AsyncClient, strategy: str) -> None:
    response = await client.post(f"/search/{strategy}", json={"query": "reembolso"})
    assert response.status_code == 200
    assert [hit["id"] for hit in response.json()["hits"]] == ["policy::0000"]


async def test_citation_resolves_and_private_source_is_hidden(client: AsyncClient) -> None:
    response = await client.post("/rag/extractive", json={"query": "reembolso"})
    assert response.status_code == 200
    citation = response.json()["citations"][0]
    source = await client.get(citation["href"])
    assert source.status_code == 200
    assert citation["quote"] in source.json()["text"]
    assert (
        await client.get("/sources/secret::0000", headers={"X-ACL": "internal"})
    ).status_code == 404


@pytest.mark.parametrize("strategy", ["rewrite", "hyde", "subquestions", "router", "auto"])
async def test_cloud_strategies_are_disabled(client: AsyncClient, strategy: str) -> None:
    response = await client.post(f"/search/{strategy}", json={"query": "reembolso"})
    assert response.status_code == 503
    assert (
        await client.post("/rag/llm-extractive", json={"query": "reembolso"})
    ).status_code == 503


@pytest.mark.parametrize(
    "body",
    [
        {"query": " "},
        {"query": "a", "top_k": 21},
        {"query": "a", "candidate_k": 101},
        {"query": "a", "filters": {"acl": "internal"}},
        {"query": "a", "scope": "internal"},
    ],
)
async def test_invalid_requests(client: AsyncClient, body: dict[str, object]) -> None:
    assert (await client.post("/search/hybrid", json=body)).status_code == 422


async def test_schema_registers_each_strategy(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    assert all(f"/search/{strategy}" in schema["paths"] for strategy in STRATEGIES)


@pytest.mark.parametrize("engine", ["native", "llamaindex"])
async def test_loader_limits_without_modifying_dataset(tmp_path: Path, engine: str) -> None:
    source = tmp_path / "documents.jsonl"
    document = {
        "id": "policy",
        "title": "Reembolsos",
        "doc_type": "policy",
        "language": "es",
        "content": "Reembolso hasta 48 horas antes.",
        "metadata": {"category": "candlelit"},
    }
    original = json.dumps(document) + "\n" + json.dumps({**document, "id": "second"}) + "\n"
    source.write_text(original, encoding="utf-8")
    runtime = await build_local_runtime(
        RetrievalSettings(
            _env_file=None,
            source="synthetic",
            documents_path=source,
            max_documents=1,
            engine=engine,
        )
    )
    assert runtime.documents == 1
    assert runtime.chunks == 1
    assert source.read_text(encoding="utf-8") == original
    parent = await runtime.service.source("policy::parent")
    assert parent is not None and parent.text == document["content"]


async def test_auto_merge_endpoint_has_explicit_capability(client: AsyncClient) -> None:
    health = (await client.get("/health")).json()
    response = await client.post("/search/auto-merge", json={"query": "reembolso"})
    if health["engine"] == "native":
        assert response.status_code == 503
    else:
        assert response.status_code == 200
        assert response.json()["hits"][0]["id"] == "policy::0000"


@pytest.mark.integration
async def test_postgres_snapshot_is_shared_by_both_engines() -> None:
    native = await build_local_runtime(
        RetrievalSettings(
            _env_file=None,
            engine="native",
            source="postgres",
            index_name="knowledge",
            max_documents=500,
        )
    )
    framework = await build_local_runtime(
        RetrievalSettings(
            _env_file=None,
            engine="llamaindex",
            source="postgres",
            index_name="knowledge",
            max_documents=500,
        )
    )
    assert native.documents == framework.documents
    assert native.chunks == framework.chunks
    for runtime in (native, framework):
        response = await runtime.service.answer(
            RetrievalRequest(
                "refund",
                filters={"language": "en", "category": "candlelit"},
            )
        )
        for citation in response.citations:
            source = await runtime.service.source(citation.source_id)
            assert source is not None and citation.quote in source.text


async def test_comparison_handles_empty_corpus_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads = 0

    async def counted_loader(
        settings: RetrievalSettings,
        embeddings: HashingEmbedding,
    ) -> tuple[list[VectorRecord], list[VectorRecord]]:
        nonlocal loads
        loads += 1
        return await load_synthetic(settings, embeddings)

    monkeypatch.setattr("retrieval.compare.load_synthetic", counted_loader)
    source = tmp_path / "documents.jsonl"
    source.write_text("", encoding="utf-8")
    rows = await compare(
        RetrievalSettings(
            _env_file=None,
            source="synthetic",
            documents_path=source,
        ),
        "refund",
        filters={},
    )
    assert len(rows) == 9
    assert loads == 1
    assert {row.engine for row in rows} == {"native", "llamaindex"}
    assert all(row.documents == row.chunks == 0 and not row.source_ids for row in rows)
    assert source.read_text(encoding="utf-8") == ""


def test_acl_defaults_and_explicit_restrictions() -> None:
    assert access_metadata({"doc_type": "policy"})["acl"] == "public"
    assert access_metadata({"doc_type": "handbook"})["acl"] == "internal"
    assert access_metadata({"doc_type": "policy", "acl": "internal"})["acl"] == "internal"
    assert access_metadata({"doc_type": "unknown"})["acl"] == "internal"

"""Contrato HTTP y arranque local, sin red ni coste cloud."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from headache_core.adapters.local_retrieval import LocalRetrieval, TokenCoverageReranker
from headache_core.adapters.memory import HashingEmbedding
from headache_core.application.retrieval import RetrievalService
from headache_core.ports.vector_store import VectorRecord
from retrieval.app import STRATEGIES, create_app
from retrieval.bootstrap import access_metadata, build_local_runtime
from retrieval.settings import RetrievalSettings


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
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
    service = RetrievalService(
        LocalRetrieval(records, embeddings=embeddings), reranker=TokenCoverageReranker()
    )
    async with AsyncClient(
        transport=ASGITransport(app=create_app(service)), base_url="http://test"
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


async def test_loader_limits_without_modifying_dataset(tmp_path: Path) -> None:
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
        )
    )
    assert runtime.documents == 1
    assert runtime.chunks == 1
    assert source.read_text(encoding="utf-8") == original
    parent = await runtime.service.source("policy::parent")
    assert parent is not None and parent.text == document["content"]


def test_acl_defaults_and_explicit_restrictions() -> None:
    assert access_metadata({"doc_type": "policy"})["acl"] == "public"
    assert access_metadata({"doc_type": "handbook"})["acl"] == "internal"
    assert access_metadata({"doc_type": "policy", "acl": "internal"})["acl"] == "internal"
    assert access_metadata({"doc_type": "unknown"})["acl"] == "internal"

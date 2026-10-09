"""Composicion cloud explicita: se construye sin red y por defecto todo sigue local."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from headache_core.adapters.azure_retrieval import AzureSearchRetrieval
from headache_core.adapters.bedrock_retrieval import BedrockKnowledgeBase, BedrockReranker
from headache_core.adapters.foundry_iq import FoundryIQKnowledge
from headache_core.adapters.local_retrieval import LocalRetrieval
from headache_core.adapters.memory import HashingEmbedding
from headache_core.application.retrieval import RetrievalService
from headache_core.config import Settings
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.retrieval import (
    ManagedCitation,
    ManagedRagResponse,
    ManagedReference,
    ManagedSearchMode,
)
from headache_core.ports.vector_store import MetadataValue
from retrieval import cloud
from retrieval.app import create_app
from retrieval.bootstrap import build_runtime
from retrieval.cloud import CloudParts, build_cloud_parts
from retrieval.settings import RetrievalSettings


def cloud_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "llm_provider": "azure",
        "azure_openai": {"endpoint": "https://headache.openai.azure.com"},
        "azure_search": {"endpoint": "https://headache.search.windows.net"},
        "bedrock": {
            "region": "eu-west-1",
            "rerank_model_arn": "arn:aws:bedrock:eu-west-1::foundation-model/cohere.rerank-v3-5:0",
        },
        "bedrock_kb": {"region": "eu-west-1", "knowledge_base_id": "KB12345678"},
        "foundry_iq": {
            "project_endpoint": "https://headache.services.ai.azure.com/api/projects/poc"
        },
        "langfuse": {"public_key": ""},
    }
    return Settings(_env_file=None, **(values | overrides))


def config(**values: Any) -> RetrievalSettings:
    return RetrievalSettings(_env_file=None, **values)


@pytest.fixture(autouse=True)
def no_aws_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(variable, raising=False)


def test_defaults_are_local_only() -> None:
    settings = config()
    assert not settings.uses_cloud
    assert (settings.backend, settings.reranker, settings.llm, settings.managed) == (
        "local",
        "token-coverage",
        "none",
        "none",
    )


@pytest.mark.parametrize(
    "values",
    [
        {"embeddings": "azure"},
        {"semantic_ranker": True},
        {"backend": "azure_search"},
        {"backend": "bogus"},
    ],
)
def test_incoherent_compositions_fail_at_startup(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        config(**values)


async def test_full_cloud_composition_builds_without_network() -> None:
    parts = await build_cloud_parts(
        config(
            backend="azure_search",
            embeddings="azure",
            semantic_ranker=True,
            reranker="bedrock",
            llm="gateway",
            managed="bedrock_kb",
        ),
        cloud_settings(),
    )
    try:
        assert isinstance(parts.backend, AzureSearchRetrieval)
        assert isinstance(parts.reranker, BedrockReranker)
        assert isinstance(parts.managed, BedrockKnowledgeBase)
        assert parts.llm is not None
        assert parts.components == {
            "backend": "azure_search:azure+semantic-ranker",
            "reranker": "bedrock-rerank",
            "llm": "gateway:native:azure",
            "managed": "bedrock_kb",
        }
    finally:
        await parts.aclose()


@pytest.mark.parametrize("project_endpoint", ["", "https://h.services.ai.azure.com/api/projects/p"])
async def test_foundry_iq_composition(project_endpoint: str) -> None:
    settings = cloud_settings(foundry_iq={"project_endpoint": project_endpoint})
    parts = await build_cloud_parts(config(managed="foundry_iq"), settings)
    try:
        assert isinstance(parts.managed, FoundryIQKnowledge)
        assert parts.components == {"managed": "foundry_iq"}
        if not project_endpoint:
            with pytest.raises(Exception, match="Agent Service"):
                await parts.managed.answer("refund", top_k=5, filters={}, scope=AccessScope())
    finally:
        await parts.aclose()


@pytest.mark.parametrize(
    ("values", "overrides", "variable"),
    [
        ({"backend": "azure_search", "embeddings": "azure"}, {"azure_search": {}}, "AZURE_SEARCH"),
        ({"reranker": "bedrock"}, {"bedrock": {"rerank_model_arn": ""}}, "RERANK_MODEL_ARN"),
        ({"managed": "bedrock_kb"}, {"bedrock_kb": {}}, "KNOWLEDGE_BASE_ID"),
        ({"managed": "foundry_iq"}, {"azure_search": {}}, "AZURE_SEARCH"),
    ],
)
async def test_missing_configuration_fails_fast(
    values: dict[str, Any], overrides: dict[str, Any], variable: str
) -> None:
    with pytest.raises(ValueError, match=variable):
        await build_cloud_parts(config(**values), cloud_settings(**overrides))


async def test_partial_composition_is_closed_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    async def record_close(self: CloudParts) -> None:
        closed.append(bool(self.closers))

    monkeypatch.setattr(cloud.CloudParts, "aclose", record_close)
    with pytest.raises(ValueError, match="RERANK_MODEL_ARN"):
        await build_cloud_parts(
            config(backend="azure_search", embeddings="azure", reranker="bedrock"),
            cloud_settings(bedrock={"rerank_model_arn": ""}),
        )
    assert closed == [True]


def corpus(tmp_path: Path) -> Path:
    source = tmp_path / "documents.jsonl"
    document = {
        "id": "policy",
        "title": "Refunds",
        "doc_type": "policy",
        "language": "en",
        "content": "Refund within 48 hours.",
        "metadata": {},
    }
    source.write_text(json.dumps(document) + "\n", encoding="utf-8")
    return source


async def test_local_runtime_has_no_cloud_parts(tmp_path: Path) -> None:
    runtime = await build_runtime(config(documents_path=corpus(tmp_path)), cloud_settings())
    assert runtime.managed is None and runtime.components == {} and runtime.close is None
    await runtime.aclose()


async def test_local_corpus_with_cloud_reranker_keeps_local_backend(tmp_path: Path) -> None:
    runtime = await build_runtime(
        config(documents_path=corpus(tmp_path), reranker="bedrock"), cloud_settings()
    )
    try:
        assert runtime.documents == 1 and runtime.source == "synthetic"
        assert runtime.components == {"reranker": "bedrock-rerank"}
    finally:
        await runtime.aclose()


class ManagedDouble:
    def __init__(self) -> None:
        self.scopes: list[AccessScope] = []
        self.reference = ManagedReference("policy::0000", "policy", "Policy", "Refund.", "uri")

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
        mode: ManagedSearchMode = "SEMANTIC",
    ) -> list[ManagedReference]:
        self.scopes.append(scope)
        return [self.reference]

    async def answer(
        self,
        query: str,
        *,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
        mode: ManagedSearchMode = "SEMANTIC",
    ) -> ManagedRagResponse:
        self.scopes.append(scope)
        citation = ManagedCitation("You can get a refund.", 0, 21, (self.reference,))
        return ManagedRagResponse("You can get a refund.", (citation,), False)


async def managed_client(managed: ManagedDouble | None) -> AsyncClient:
    embeddings = HashingEmbedding(16)
    service = RetrievalService(LocalRetrieval([], embeddings=embeddings))
    app = create_app(service, config(), managed=managed)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_managed_routes_use_public_scope_only() -> None:
    managed = ManagedDouble()
    async with await managed_client(managed) as client:
        retrieved = await client.post("/managed/retrieve", json={"query": "refund"})
        answered = await client.post("/managed/answer", json={"query": "refund"})
        rejected = await client.post("/managed/answer", json={"query": "x", "scope": "internal"})
    assert retrieved.status_code == 200 and retrieved.json()[0]["source_id"] == "policy::0000"
    body = answered.json()
    assert body["citations"][0]["references"][0]["uri"] == "uri" and not body["abstained"]
    assert rejected.status_code == 422
    assert managed.scopes == [AccessScope(), AccessScope()]


async def test_managed_routes_are_unavailable_by_default() -> None:
    async with await managed_client(None) as client:
        for route in ("/managed/retrieve", "/managed/answer"):
            assert (await client.post(route, json={"query": "refund"})).status_code == 503
        health = (await client.get("/health")).json()
    assert health["cloud_calls"] is False and health["managed"] == "none"

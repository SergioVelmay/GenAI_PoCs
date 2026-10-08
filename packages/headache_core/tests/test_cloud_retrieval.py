"""Contratos cloud con SDK y dobles locales, sin red ni coste."""

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from azure.core.exceptions import ResourceNotFoundError
from botocore.model import StringShape
from botocore.stub import Stubber

from headache_core.adapters.azure_retrieval import AzureSearchRetrieval
from headache_core.adapters.bedrock_retrieval import (
    BedrockKnowledgeBase,
    BedrockReranker,
    knowledge_filter,
)
from headache_core.adapters.memory import HashingEmbedding
from headache_core.application.retrieval import (
    RetrievalRequest,
    RetrievalService,
    RetrievalUnavailableError,
)
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.retrieval import SearchMode
from headache_core.ports.vector_store import SearchResult, VectorRecord

if TYPE_CHECKING:
    from mypy_boto3_bedrock_agent_runtime import AgentsforBedrockRuntimeClient


def search_document(acl: str = "public") -> dict[str, Any]:
    return {
        "chunk_id": "policy::0000",
        "text": "Refund within 48 hours.",
        "metadata_json": json.dumps({"acl": acl, "language": "en", "document_id": "policy"}),
        "@search.score": 0.03,
        "@search.rerankerScore": 3.5,
    }


class SearchClientDouble:
    def __init__(self, document: dict[str, Any] | None = None) -> None:
        self.document = document if document is not None else search_document()
        self.calls: list[dict[str, Any]] = []
        self.missing = False

    async def search(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        self.calls.append(kwargs)

        async def results() -> AsyncIterator[dict[str, Any]]:
            yield self.document

        return results()

    async def get_document(self, **kwargs: Any) -> dict[str, Any]:
        if self.missing:
            raise ResourceNotFoundError("not found")
        return self.document


@pytest.mark.parametrize("mode", ["bm25", "vector", "hybrid"])
@pytest.mark.parametrize("semantic", [False, True])
async def test_azure_request_prefilters_and_maps_ranker(mode: SearchMode, semantic: bool) -> None:
    client = SearchClientDouble()
    adapter = AzureSearchRetrieval(client, embeddings=HashingEmbedding(64), semantic=semantic)  # type: ignore[arg-type]
    results = await adapter.search(
        "refund", mode=mode, top_k=5, filters={"language": "en"}, scope=AccessScope()
    )
    request = client.calls[0]
    assert request["filter"] == "(acl eq 'public') and (language eq 'en')"
    assert request["search_text"] == (None if mode == "vector" else "refund")
    if mode != "bm25":
        assert request["vector_filter_mode"] == "preFilter"
        assert request["vector_queries"][0].k_nearest_neighbors == (50 if semantic else 5)
    else:
        assert "vector_queries" not in request
    assert results[0].score == (3.5 if semantic else 0.03)
    if semantic:
        assert request["semantic_query"] == "refund"
        assert request["semantic_error_mode"] == "fail"


async def test_azure_citations_resolve_without_mutating_sources() -> None:
    client = SearchClientDouble()
    adapter = AzureSearchRetrieval(client, embeddings=HashingEmbedding(64))  # type: ignore[arg-type]
    service = RetrievalService(adapter)
    answer = await service.answer(RetrievalRequest("refund"))
    source = await service.source(answer.citations[0].source_id)
    assert source is not None and answer.citations[0].quote in source.text
    client.document = search_document("internal")
    assert await service.source("policy::0000") is None
    with pytest.raises(ValueError, match="alcance"):
        await service.retrieve(RetrievalRequest("refund"))
    client.missing = True
    assert await service.source("absent") is None


async def test_azure_deny_empty_scope_and_reject_acl_injection() -> None:
    client = SearchClientDouble()
    adapter = AzureSearchRetrieval(client, embeddings=HashingEmbedding(64))  # type: ignore[arg-type]
    assert (
        await adapter.search("refund", mode="hybrid", top_k=5, filters={}, scope=AccessScope(()))
        == []
    )
    assert not client.calls
    with pytest.raises(ValueError, match="ACL"):
        await adapter.search(
            "refund", mode="hybrid", top_k=5, filters={"acl": "internal"}, scope=AccessScope()
        )


async def test_azure_missing_semantic_score_is_not_a_silent_fallback() -> None:
    document = search_document()
    del document["@search.rerankerScore"]
    client = SearchClientDouble(document)
    adapter = AzureSearchRetrieval(client, embeddings=HashingEmbedding(64), semantic=True)  # type: ignore[arg-type]
    with pytest.raises(RetrievalUnavailableError):
        await adapter.search("refund", mode="hybrid", top_k=5, filters={}, scope=AccessScope())


RERANK_MODEL = "arn:aws:bedrock:eu-west-1::foundation-model/cohere.rerank-v3-5:0"
GENERATION_MODEL = "arn:aws:bedrock:eu-west-1::foundation-model/amazon.nova-lite-v1:0"
KB_ID = "KB12345678"


def runtime_client() -> "AgentsforBedrockRuntimeClient":
    return boto3.client(
        "bedrock-agent-runtime",
        region_name="eu-west-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


async def test_bedrock_rerank_preserves_candidate_identity() -> None:
    client = runtime_client()
    records = [
        VectorRecord(str(index), text, [], {"acl": "public"})
        for index, text in enumerate(["Refund within 48 hours.", "Venue opening times."])
    ]
    candidates = [SearchResult(record, 0.03) for record in records]
    expected: dict[str, Any] = {
        "queries": [{"type": "TEXT", "textQuery": {"text": "refund"}}],
        "sources": [
            {
                "type": "INLINE",
                "inlineDocumentSource": {
                    "type": "TEXT",
                    "textDocument": {"text": record.text},
                },
            }
            for record in records
        ],
        "rerankingConfiguration": {
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {"modelArn": RERANK_MODEL},
                "numberOfResults": 2,
            },
        },
    }
    shape = client.meta.service_model.shape_for("RerankingConfigurationType")
    assert isinstance(shape, StringShape)
    assert expected["rerankingConfiguration"]["type"] in shape.enum
    with Stubber(client) as stub:
        stub.add_response(
            "rerank",
            {
                "results": [
                    {
                        "index": 1,
                        "relevanceScore": 0.9,
                        "document": {"type": "TEXT", "textDocument": {"text": "fabricated"}},
                    },
                    {"index": 0, "relevanceScore": 0.5},
                ]
            },
            expected,
        )
        result = await BedrockReranker(client, model_arn=RERANK_MODEL).rerank(
            "refund", candidates, top_k=2
        )
        assert result[0].record is records[1]
        assert result[0].record.text == "Venue opening times."
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "results",
    [
        [{"index": 1, "relevanceScore": 0.9}],
        [{"index": 0, "relevanceScore": 0.9}, {"index": 0, "relevanceScore": 0.8}],
    ],
)
async def test_bedrock_rerank_rejects_invalid_indexes(results: list[dict[str, Any]]) -> None:
    client = runtime_client()
    with Stubber(client) as stub:
        stub.add_response("rerank", {"results": results})
        with pytest.raises(ValueError, match="Indice"):
            await BedrockReranker(client, model_arn=RERANK_MODEL).rerank(
                "refund",
                [SearchResult(VectorRecord("one", "Refund", [], {}), 1.0)],
                top_k=1,
            )


def kb_reference(acl: str = "public") -> dict[str, Any]:
    return {
        "content": {"text": "Refund within 48 hours."},
        "location": {"type": "S3", "s3Location": {"uri": "s3://headache-corpus/policy.md"}},
        "metadata": {"acl": acl, "document_id": "policy", "language": "en", "title": "Policy"},
    }


def kb_configuration() -> dict[str, Any]:
    return {
        "vectorSearchConfiguration": {
            "numberOfResults": 5,
            "overrideSearchType": "SEMANTIC",
            "filter": {
                "andAll": [
                    {"equals": {"key": "acl", "value": "public"}},
                    {"equals": {"key": "language", "value": "en"}},
                ]
            },
        }
    }


async def test_bedrock_retrieve_sends_acl_and_canonicalizes_references() -> None:
    client = runtime_client()
    reference = kb_reference()
    with Stubber(client) as stub:
        stub.add_response(
            "retrieve",
            {"retrievalResults": [dict(reference, score=0.9), dict(reference, score=0.8)]},
            {
                "knowledgeBaseId": KB_ID,
                "retrievalQuery": {"text": "refund"},
                "retrievalConfiguration": kb_configuration(),
            },
        )
        result = await BedrockKnowledgeBase(client, knowledge_base_id=KB_ID).retrieve(
            "refund",
            top_k=5,
            filters={"language": "en"},
            scope=AccessScope(),
        )
        assert len(result) == 1 and result[0].document_id == "policy"
        assert result[0].uri == "s3://headache-corpus/policy.md"
        assert len(result[0].source_id) == 64
        stub.assert_no_pending_responses()


def generated_response() -> dict[str, Any]:
    return {
        "output": {"text": "You may request a refund."},
        "sessionId": "session-1",
        "citations": [
            {
                "generatedResponsePart": {
                    "textResponsePart": {
                        "text": "You may request a refund.",
                        "span": {"start": 0, "end": 24},
                    }
                },
                "retrievedReferences": [kb_reference()],
            }
        ],
    }


async def test_bedrock_managed_answer_has_distinct_generated_and_source_text() -> None:
    client = runtime_client()
    with Stubber(client) as stub:
        stub.add_response(
            "retrieve_and_generate",
            generated_response(),
            {
                "input": {"text": "refund"},
                "retrieveAndGenerateConfiguration": {
                    "type": "KNOWLEDGE_BASE",
                    "knowledgeBaseConfiguration": {
                        "knowledgeBaseId": KB_ID,
                        "modelArn": GENERATION_MODEL,
                        "retrievalConfiguration": kb_configuration(),
                    },
                },
            },
        )
        result = await BedrockKnowledgeBase(
            client, knowledge_base_id=KB_ID, model_arn=GENERATION_MODEL
        ).answer(
            "refund",
            top_k=5,
            filters={"language": "en"},
            scope=AccessScope(),
        )
        assert not result.abstained
        citation = result.citations[0]
        assert result.answer[citation.start : citation.end] == citation.text
        assert citation.text != citation.references[0].text
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "defect", ["private", "missing-acl", "bad-span", "no-reference", "uncited", "wrong-filter"]
)
async def test_bedrock_managed_citations_fail_closed(defect: str) -> None:
    response = generated_response()
    citation = response["citations"][0]
    if defect == "private":
        citation["retrievedReferences"] = [kb_reference("internal")]
    elif defect == "missing-acl":
        del citation["retrievedReferences"][0]["metadata"]["acl"]
    elif defect == "bad-span":
        citation["generatedResponsePart"]["textResponsePart"]["span"]["start"] = 1
    elif defect == "no-reference":
        citation["retrievedReferences"] = []
    elif defect == "uncited":
        response["output"]["text"] += " Also free tickets."
    else:
        citation["retrievedReferences"][0]["metadata"]["language"] = "es"
    client = runtime_client()
    with Stubber(client) as stub:
        stub.add_response("retrieve_and_generate", response)
        from headache_core.application.retrieval import InvalidCitationError

        with pytest.raises(InvalidCitationError):
            await BedrockKnowledgeBase(
                client, knowledge_base_id=KB_ID, model_arn=GENERATION_MODEL
            ).answer(
                "refund",
                top_k=5,
                filters={"language": "en"},
                scope=AccessScope(),
            )


async def test_bedrock_empty_scope_and_unsupported_hybrid_make_no_requests() -> None:
    client = runtime_client()
    adapter = BedrockKnowledgeBase(client, knowledge_base_id=KB_ID)
    with Stubber(client):
        assert await adapter.retrieve("refund", top_k=5, filters={}, scope=AccessScope(())) == []
        assert (
            await adapter.answer("refund", top_k=5, filters={}, scope=AccessScope(()))
        ).abstained
        with pytest.raises(RetrievalUnavailableError, match="HYBRID"):
            await adapter.retrieve(
                "refund", top_k=5, filters={}, scope=AccessScope(), mode="HYBRID"
            )
        with pytest.raises(RetrievalUnavailableError, match="ARN"):
            await adapter.answer("refund", top_k=5, filters={}, scope=AccessScope())
        with pytest.raises(ValueError):
            await adapter.retrieve(
                "refund", top_k=5, filters={"acl": "internal"}, scope=AccessScope()
            )


def test_bedrock_multiple_acls_are_grouped_before_metadata_filters() -> None:
    predicate = knowledge_filter({"language": "en"}, AccessScope(("public", "internal")))
    assert predicate == {
        "andAll": [
            {
                "orAll": [
                    {"equals": {"key": "acl", "value": "public"}},
                    {"equals": {"key": "acl", "value": "internal"}},
                ]
            },
            {"equals": {"key": "language", "value": "en"}},
        ]
    }


@pytest.mark.parametrize("intervened", [False, True])
async def test_bedrock_managed_abstains_without_evidence_or_after_guardrail(
    intervened: bool,
) -> None:
    response = generated_response()
    if intervened:
        response["guardrailAction"] = "INTERVENED"
    else:
        response["citations"] = []
    client = runtime_client()
    with Stubber(client) as stub:
        stub.add_response("retrieve_and_generate", response)
        result = await BedrockKnowledgeBase(
            client,
            knowledge_base_id=KB_ID,
            model_arn=GENERATION_MODEL,
        ).answer("refund", top_k=5, filters={"language": "en"}, scope=AccessScope())
        assert result.abstained and result.answer == "" and not result.citations


@pytest.mark.parametrize("operation", ["rerank", "retrieve"])
async def test_bedrock_pagination_cannot_silently_truncate(operation: str) -> None:
    client = runtime_client()
    with Stubber(client) as stub:
        if operation == "rerank":
            stub.add_response(
                operation, {"results": [{"index": 0, "relevanceScore": 1.0}], "nextToken": "next"}
            )
            with pytest.raises(RetrievalUnavailableError, match="paginada"):
                await BedrockReranker(client, model_arn=RERANK_MODEL).rerank(
                    "refund",
                    [SearchResult(VectorRecord("one", "Refund", [], {}), 1.0)],
                    top_k=1,
                )
        else:
            stub.add_response(
                operation,
                {"retrievalResults": [dict(kb_reference(), score=1.0)], "nextToken": "next"},
            )
            with pytest.raises(RetrievalUnavailableError, match="paginado"):
                await BedrockKnowledgeBase(client, knowledge_base_id=KB_ID).retrieve(
                    "refund",
                    top_k=5,
                    filters={},
                    scope=AccessScope(),
                )


@pytest.mark.parametrize("acl", ["internal", None])
async def test_bedrock_retrieve_rejects_private_or_unclassified_sources(acl: str | None) -> None:
    reference = kb_reference()
    if acl is None:
        del reference["metadata"]["acl"]
    else:
        reference["metadata"]["acl"] = acl
    client = runtime_client()
    with Stubber(client) as stub:
        stub.add_response("retrieve", {"retrievalResults": [dict(reference, score=1.0)]})
        from headache_core.application.retrieval import InvalidCitationError

        with pytest.raises(InvalidCitationError):
            await BedrockKnowledgeBase(client, knowledge_base_id=KB_ID).retrieve(
                "refund",
                top_k=5,
                filters={},
                scope=AccessScope(),
            )


@pytest.mark.parametrize("score", [float("nan"), float("inf")])
async def test_azure_rejects_nonfinite_scores(score: float) -> None:
    document = search_document()
    document["@search.score"] = score
    client = SearchClientDouble(document)
    adapter = AzureSearchRetrieval(client, embeddings=HashingEmbedding(64))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finito"):
        await adapter.search("refund", mode="bm25", top_k=5, filters={}, scope=AccessScope())

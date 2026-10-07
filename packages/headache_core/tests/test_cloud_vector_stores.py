"""Carril B en la nube sin red: embeddings (Azure OpenAI, Bedrock), AI Search y S3 Vectors."""

from __future__ import annotations

import io
import json
from typing import Any

import boto3
import httpx
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber
from openai import AsyncAzureOpenAI

from headache_core.adapters.azure_search import (
    AzureAISearchVectorStore,
    build_index,
    document_key,
    odata_filter,
    to_document,
)
from headache_core.adapters.cloud_embeddings import AzureOpenAIEmbedding, BedrockTitanEmbedding
from headache_core.adapters.s3_vectors import S3VectorStore, s3_filter
from headache_core.ports import VectorRecord

RECORD = VectorRecord(
    id="policy-candlelit-en::0000",
    text="Refundable up to 48 hours before the start.",
    embedding=[0.1, 0.2, 0.3],
    metadata={"document_id": "policy-candlelit-en", "language": "en", "chunk": 0, "title": "T"},
)


async def test_azure_embeddings_request_1024_dimensions_and_keep_order() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        # La API puede devolver los vectores desordenados: manda `index`.
        data = [
            {"object": "embedding", "index": 1, "embedding": [2.0]},
            {"object": "embedding", "index": 0, "embedding": [1.0]},
        ]
        usage = {"prompt_tokens": 4, "total_tokens": 4}
        return httpx.Response(
            200, json={"object": "list", "data": data, "model": "m", "usage": usage}
        )

    client = AsyncAzureOpenAI(
        azure_endpoint="https://headache-test.openai.azure.com",
        api_key="test",
        api_version="2024-10-21",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    embedding = AzureOpenAIEmbedding(client, "text-embedding-3-small")

    vectors = await embedding.embed_texts(["a", "b"])

    assert vectors == [[1.0], [2.0]]
    assert seen["dimensions"] == 1024
    assert seen["model"] == "text-embedding-3-small"


async def test_bedrock_titan_sends_dimensions_and_normalize() -> None:
    client = boto3.client(
        "bedrock-runtime", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    payload = json.dumps({"embedding": [0.5, 0.5], "inputTextTokenCount": 3}).encode()
    stubber = Stubber(client)
    stubber.add_response(
        "invoke_model",
        {
            "body": StreamingBody(io.BytesIO(payload), len(payload)),
            "contentType": "application/json",
        },
        {
            "modelId": "amazon.titan-embed-text-v2:0",
            "body": json.dumps({"inputText": "hola", "dimensions": 1024, "normalize": True}),
            "contentType": "application/json",
            "accept": "application/json",
        },
    )
    with stubber:
        vector = await BedrockTitanEmbedding(client, "amazon.titan-embed-text-v2:0").embed_query(
            "hola"
        )

    assert vector == [0.5, 0.5]
    stubber.assert_no_pending_responses()


def test_ai_search_document_uses_safe_key_and_flattens_filterable_fields() -> None:
    document = to_document(RECORD)

    assert document["id"] == document_key(RECORD.id)
    assert ":" not in document["id"]
    assert document["chunk_id"] == RECORD.id
    assert document["language"] == "en"
    assert document["chunk"] == 0
    assert "title" not in document  # No filtrable: solo dentro de metadata_json.
    assert json.loads(document["metadata_json"])["title"] == "T"


def test_ai_search_odata_filter_escapes_and_rejects_undeclared_fields() -> None:
    assert odata_filter({"city": "L'Hospitalet", "chunk": 0}) == (
        "city eq 'L''Hospitalet' and chunk eq 0"
    )
    with pytest.raises(ValueError, match="no es filtrable"):
        odata_filter({"title": "x"})


def test_ai_search_index_declares_vector_field_and_filters() -> None:
    index = build_index("knowledge", 1024).as_dict()
    fields = {f["name"]: f for f in index["fields"]}

    assert fields["id"]["key"] is True
    assert fields["embedding"]["dimensions"] == 1024
    assert fields["language"]["filterable"] is True
    assert index["vectorSearch"]["algorithms"][0]["hnswParameters"]["metric"] == "cosine"


class FakeSearchClient:
    def __init__(self) -> None:
        self.uploaded: list[dict[str, Any]] = []
        self.search_kwargs: dict[str, Any] = {}

    async def merge_or_upload_documents(self, documents: list[dict[str, Any]]) -> list[Any]:
        self.uploaded.extend(documents)
        return [type("R", (), {"key": d["id"], "succeeded": True})() for d in documents]

    async def search(self, **kwargs: Any) -> Any:
        self.search_kwargs = kwargs
        doc = {
            "chunk_id": RECORD.id,
            "text": RECORD.text,
            "metadata_json": json.dumps(RECORD.metadata),
            "@search.score": 0.9,
        }

        async def results() -> Any:
            yield doc

        return results()


async def test_ai_search_store_prefilters_and_maps_results() -> None:
    client = FakeSearchClient()
    store = AzureAISearchVectorStore(client)  # type: ignore[arg-type]

    await store.upsert([RECORD])
    hits = await store.search([0.1, 0.2, 0.3], top_k=3, filters={"language": "en"})

    assert len(client.uploaded) == 1
    assert client.search_kwargs["filter"] == "language eq 'en'"
    assert client.search_kwargs["vector_filter_mode"] == "preFilter"
    assert hits[0].record.id == RECORD.id
    assert hits[0].record.metadata["document_id"] == "policy-candlelit-en"


def test_s3_filter_uses_eq_and_and() -> None:
    assert s3_filter({"language": "en"}) == {"language": {"$eq": "en"}}
    assert s3_filter({"language": "en", "chunk": 0}) == {
        "$and": [{"language": {"$eq": "en"}}, {"chunk": {"$eq": 0}}]
    }


async def test_s3_vectors_store_puts_text_as_metadata_and_converts_distance() -> None:
    client = boto3.client(
        "s3vectors", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    stubber = Stubber(client)
    stubber.add_response(
        "put_vectors",
        {},
        {
            "vectorBucketName": "bucket",
            "indexName": "knowledge",
            "vectors": [
                {
                    "key": RECORD.id,
                    "data": {"float32": RECORD.embedding},
                    "metadata": {**RECORD.metadata, "text": RECORD.text},
                }
            ],
        },
    )
    stubber.add_response(
        "query_vectors",
        {
            "vectors": [
                {
                    "key": RECORD.id,
                    "distance": 0.25,
                    "metadata": {**RECORD.metadata, "text": RECORD.text},
                }
            ],
            "distanceMetric": "cosine",
        },
        {
            "vectorBucketName": "bucket",
            "indexName": "knowledge",
            "queryVector": {"float32": [0.1, 0.2, 0.3]},
            "topK": 2,
            "returnMetadata": True,
            "returnDistance": True,
            "filter": {"language": {"$eq": "en"}},
        },
    )
    store = S3VectorStore(client, bucket="bucket", index="knowledge")

    with stubber:
        await store.upsert([RECORD])
        hits = await store.search([0.1, 0.2, 0.3], top_k=2, filters={"language": "en"})

    assert hits[0].score == pytest.approx(0.75)
    assert hits[0].record.text == RECORD.text
    assert "text" not in hits[0].record.metadata
    stubber.assert_no_pending_responses()

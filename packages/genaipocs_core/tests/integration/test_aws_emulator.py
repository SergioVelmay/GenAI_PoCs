"""Adaptadores de AWS reales contra el AWS Emulator oficial (checkpoint G→13.2), sin coste.

    docker compose --profile aws up -d
    uv run pytest -m integration packages/genaipocs_core/tests/integration/test_aws_emulator.py

El test hace de "Terraform": crea el bucket de vectores, el índice y el bucket de documentos con
la misma forma que `infra/terraform/aws/main.tf` y `lane_a.tf`. El emulador no evalúa IAM: un
verde aquí prueba el contrato de la API, no los permisos (eso sigue en la nube).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from genaipocs_core.adapters.bedrock_kb import (
    BedrockKnowledgeBaseIngestion,
    metadata_sidecar,
    object_key,
)
from genaipocs_core.adapters.memory import HashingEmbedding
from genaipocs_core.adapters.s3_vectors import TEXT_KEY, S3VectorStore
from genaipocs_core.domain import DocumentType, KnowledgeDocument
from genaipocs_core.ports import VectorRecord

pytestmark = pytest.mark.integration

DIMENSIONS = 256


def test_is_the_emulator_not_aws(aws_emulator: Callable[[str], Any]) -> None:
    # La cuenta única del emulador: si saliera otra, las peticiones irían a AWS.
    assert aws_emulator("sts").get_caller_identity()["Account"] == "000000000000"


@pytest.fixture
def vector_index(aws_emulator: Callable[[str], Any]) -> Iterator[tuple[Any, str, str]]:
    client = aws_emulator("s3vectors")
    bucket, index = f"it-{uuid.uuid4().hex[:12]}", "knowledge"
    client.create_vector_bucket(vectorBucketName=bucket)
    # Como `aws_s3vectors_index.knowledge`: coseno y el texto como metadato no filtrable.
    client.create_index(
        vectorBucketName=bucket,
        indexName=index,
        dataType="float32",
        dimension=DIMENSIONS,
        distanceMetric="cosine",
        metadataConfiguration={"nonFilterableMetadataKeys": [TEXT_KEY]},
    )
    yield client, bucket, index
    client.delete_index(vectorBucketName=bucket, indexName=index)
    client.delete_vector_bucket(vectorBucketName=bucket)


async def test_s3_vector_store_upsert_search_filter_and_delete(
    vector_index: tuple[Any, str, str],
) -> None:
    client, bucket, index = vector_index
    embeddings = HashingEmbedding(DIMENSIONS)
    store = S3VectorStore(client, bucket=bucket, index=index)
    texts = {
        "refund-en": ("candlelit refund policy hours", {"language": "en", "doc_type": "policy"}),
        "refund-es": (
            "politica reembolso candlelit horas",
            {"language": "es", "doc_type": "policy"},
        ),
        "doors-en": ("doors open minutes before start", {"language": "en", "doc_type": "faq"}),
    }
    vectors = await embeddings.embed_texts([t for t, _ in texts.values()])
    records = [
        VectorRecord(id=key, text=text, embedding=vector, metadata=dict(meta))
        for (key, (text, meta)), vector in zip(texts.items(), vectors, strict=True)
    ]
    await store.upsert(records)
    await store.upsert(records)  # PutVectors sobrescribe por clave: idempotente.

    query = await embeddings.embed_query("candlelit refund")
    top = await store.search(query, top_k=3)
    filtered = await store.search(query, top_k=3, filters={"language": "es"})
    both = await store.search(query, top_k=3, filters={"language": "en", "doc_type": "faq"})

    assert top[0].record.id == "refund-en"
    assert top[0].record.text == "candlelit refund policy hours"
    assert top[0].record.metadata == {"language": "en", "doc_type": "policy"}
    assert top[0].score > top[-1].score
    assert [r.record.id for r in filtered] == ["refund-es"]
    assert [r.record.id for r in both] == ["doors-en"]  # `$and` de dos `$eq`.

    await store.delete(["refund-en", "refund-es"])
    assert [r.record.id for r in await store.search(query, top_k=3)] == ["doors-en"]


async def test_bedrock_kb_upload_writes_document_and_metadata_sidecar(
    aws_emulator: Callable[[str], Any],
) -> None:
    s3 = aws_emulator("s3")
    bucket = f"it-docs-{uuid.uuid4().hex[:12]}"
    s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})
    document = KnowledgeDocument(
        id="faq-madrid-es",
        doc_type=DocumentType.FAQ,
        language="es",
        title="Preguntas frecuentes de Madrid",
        content="# Acceso\n\nLas puertas abren 30 minutos antes. Señalización en español.",
        metadata={"city": "madrid"},
    )
    # El cliente de bedrock-agent solo se usa para lanzar la ingesta (no en este test).
    ingestion = BedrockKnowledgeBaseIngestion(
        s3=s3,
        agent=aws_emulator("bedrock-agent"),
        bucket=bucket,
        knowledge_base_id="kb",
        data_source_id="ds",
    )
    try:
        await ingestion.upload(document)
        await ingestion.upload(document)  # Re-subir sobrescribe: sin duplicados.

        key = object_key(document)
        body = s3.get_object(Bucket=bucket, Key=key)
        sidecar = s3.get_object(Bucket=bucket, Key=f"{key}.metadata.json")
        listed = s3.list_objects_v2(Bucket=bucket)["Contents"]

        assert body["Body"].read().decode() == document.content  # UTF-8 intacto.
        assert body["ContentType"] == "text/markdown; charset=utf-8"
        assert json.loads(sidecar["Body"].read()) == metadata_sidecar(document)
        assert sorted(o["Key"] for o in listed) == [key, f"{key}.metadata.json"]
    finally:
        _empty_and_delete_bucket(s3, bucket)


async def test_bedrock_kb_ingestion_job_indexes_the_uploaded_documents(
    aws_emulator: Callable[[str], Any],
) -> None:
    """Carril A de punta a punta (G→13.3): subir, sincronizar y esperar el job de ingesta.

    La KB y su data source se crean como en `lane_a.tf`. El emulador completa el job y escribe
    los vectores en el índice de S3 Vectors (sin llamar a un modelo de embeddings real).
    """
    suffix = uuid.uuid4().hex[:12]
    iam, s3, vectors, agent = (
        aws_emulator(name) for name in ("iam", "s3", "s3vectors", "bedrock-agent")
    )
    role_arn = iam.create_role(
        RoleName=f"it-kb-{suffix}",
        AssumeRolePolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "bedrock.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
        ),
    )["Role"]["Arn"]
    vector_bucket, bucket = f"it-kb-{suffix}", f"it-kb-docs-{suffix}"
    vectors.create_vector_bucket(vectorBucketName=vector_bucket)
    vectors.create_index(
        vectorBucketName=vector_bucket,
        indexName="knowledge-kb",
        dataType="float32",
        dimension=1024,
        distanceMetric="cosine",
        metadataConfiguration={
            "nonFilterableMetadataKeys": ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]
        },
    )
    index_arn = vectors.get_index(vectorBucketName=vector_bucket, indexName="knowledge-kb")[
        "index"
    ]["indexArn"]
    s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})
    kb_id = agent.create_knowledge_base(
        name=f"it-kb-{suffix}",
        roleArn=role_arn,
        knowledgeBaseConfiguration={
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": (
                    "arn:aws:bedrock:eu-west-1::foundation-model/amazon.titan-embed-text-v2:0"
                ),
                "embeddingModelConfiguration": {
                    "bedrockEmbeddingModelConfiguration": {
                        "dimensions": 1024,
                        "embeddingDataType": "FLOAT32",
                    }
                },
            },
        },
        storageConfiguration={
            "type": "S3_VECTORS",
            "s3VectorsConfiguration": {"indexArn": index_arn},
        },
    )["knowledgeBase"]["knowledgeBaseId"]
    ds_id = agent.create_data_source(
        knowledgeBaseId=kb_id,
        name="docs",
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {"bucketArn": f"arn:aws:s3:::{bucket}"},
        },
    )["dataSource"]["dataSourceId"]
    ingestion = BedrockKnowledgeBaseIngestion(
        s3=s3, agent=agent, bucket=bucket, knowledge_base_id=kb_id, data_source_id=ds_id
    )
    documents = [
        KnowledgeDocument(
            id=f"faq-{city}-es",
            doc_type=DocumentType.FAQ,
            language="es",
            title=f"Preguntas frecuentes de {city}",
            content=f"Las puertas abren 30 minutos antes en {city}.",
        )
        for city in ("madrid", "sevilla")
    ]
    try:
        for document in documents:
            await ingestion.upload(document)
        job_id = await ingestion.start_sync()
        result = await ingestion.wait(job_id, poll_s=0.5)

        assert result.status == "COMPLETE"
        assert (result.scanned, result.indexed, result.failed) == (2, 2, 0)
        listed = vectors.list_vectors(vectorBucketName=vector_bucket, indexName="knowledge-kb")
        assert len(listed["vectors"]) == 2
    finally:
        agent.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id)
        agent.delete_knowledge_base(knowledgeBaseId=kb_id)
        vectors.delete_index(vectorBucketName=vector_bucket, indexName="knowledge-kb")
        vectors.delete_vector_bucket(vectorBucketName=vector_bucket)
        _empty_and_delete_bucket(s3, bucket)
        iam.delete_role(RoleName=f"it-kb-{suffix}")


def _empty_and_delete_bucket(s3: Any, bucket: str) -> None:
    for obj in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
        s3.delete_object(Bucket=bucket, Key=obj["Key"])
    s3.delete_bucket(Bucket=bucket)

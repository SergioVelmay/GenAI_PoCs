"""Carril A (gestionado) sin red: definiciones de AI Search y sincronización de Bedrock KB."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import boto3
from botocore.stub import ANY, Stubber

from headache_core.adapters import azure_search_managed as azure_lane
from headache_core.adapters.bedrock_kb import (
    BedrockKnowledgeBaseIngestion,
    metadata_sidecar,
    object_key,
)
from headache_core.domain import DocumentType, KnowledgeDocument

DOC = KnowledgeDocument(
    id="faq-mexico-city-es",
    title="Preguntas frecuentes: Mexico City",
    doc_type=DocumentType.FAQ,
    language="es",
    content="# Preguntas frecuentes\n\n¿Cuándo abren las puertas?",
    metadata={"city": "Mexico City", "country": "MX"},
)
NAMES = azure_lane.ManagedLaneNames("knowledge-managed")
TARGET = azure_lane.EmbeddingTarget(resource_url="https://headache.openai.azure.com")


def test_blob_metadata_keeps_only_ascii_declared_fields() -> None:
    doc = KnowledgeDocument(
        id="faq-x",
        title="T",
        doc_type=DocumentType.FAQ,
        language="es",
        content="c",
        metadata={"city": "Málaga", "country": "ES"},
    )

    assert azure_lane.blob_metadata(doc) == {
        "document_id": "faq-x",
        "doc_type": "faq",
        "language": "es",
    }
    assert azure_lane.blob_name(DOC) == "faq/faq-mexico-city-es.md"


def test_unchanged_blobs_are_not_reuploaded() -> None:
    # Resubir cambia la fecha de modificación y el indexer reprocesa (validado en Azure).
    md5, metadata = azure_lane.content_md5(DOC), azure_lane.blob_metadata(DOC)
    edited = KnowledgeDocument(
        DOC.id, DOC.title, DOC.doc_type, DOC.language, DOC.content + " 30 min.", DOC.metadata
    )
    retagged = KnowledgeDocument(DOC.id, DOC.title, DOC.doc_type, "en", DOC.content, DOC.metadata)

    assert not azure_lane.needs_upload(DOC, bytearray(md5), metadata)
    assert azure_lane.needs_upload(DOC, None, None)
    assert azure_lane.needs_upload(edited, md5, metadata)
    assert azure_lane.needs_upload(retagged, md5, metadata)


def test_managed_identity_mode_has_no_secrets() -> None:
    source = azure_lane.data_source(
        NAMES, credentials="ResourceId=/subscriptions/x/storageAccounts/y;", container="docs"
    ).as_dict()
    skillset = azure_lane.skillset(NAMES, TARGET).as_dict()

    assert source["credentials"]["connectionString"].startswith("ResourceId=")
    embed = next(s for s in skillset["skills"] if s["name"] == "embed")
    assert "apiKey" not in embed or embed["apiKey"] is None
    assert embed["dimensions"] == 1024


def test_skillset_projects_pages_to_chunk_index() -> None:
    skillset = azure_lane.skillset(NAMES, TARGET).as_dict()
    selector = skillset["indexProjections"]["selectors"][0]

    assert selector["targetIndexName"] == NAMES.index
    assert selector["sourceContext"] == "/document/pages/*"
    sources = {m["name"]: m["source"] for m in selector["mappings"]}
    assert sources["embedding"] == "/document/pages/*/vector"
    assert sources["language"] == "/document/language"
    assert skillset["indexProjections"]["parameters"]["projectionMode"] == (
        "skipIndexingParentDocuments"
    )


def test_split_reads_the_field_produced_by_the_markdown_parser() -> None:
    # Regresión (Azure, checkpoint 8→9): con markdown/oneToOne, `/document/content` llega vacío,
    # el SplitSkill no produce páginas y el indexer termina "success" con 0 chunks.
    indexer = azure_lane.indexer(NAMES).as_dict()
    configuration = indexer["parameters"]["configuration"]
    assert (configuration["parsingMode"], configuration["markdownParsingSubmode"]) == (
        "markdown",
        "oneToOne",
    )
    split = next(
        s for s in azure_lane.skillset(NAMES, TARGET).as_dict()["skills"] if s["name"] == "split"
    )
    assert split["inputs"] == [{"name": "text", "source": "/document/document_content"}]


def test_chunk_index_has_keyword_key_and_vectorizer() -> None:
    index = azure_lane.chunk_index(NAMES, TARGET).as_dict()
    fields = {f["name"]: f for f in index["fields"]}

    assert fields["chunk_id"]["key"] is True
    assert fields["chunk_id"]["analyzer"] == "keyword"
    assert index["vectorSearch"]["vectorizers"][0]["kind"] == "azureOpenAI"
    assert index["vectorSearch"]["profiles"][0]["vectorizer"] == "aoai-vectorizer"


def test_definitions_are_in_dependency_order() -> None:
    kinds = [
        type(d).__name__
        for d in azure_lane.definitions(NAMES, TARGET, credentials="ResourceId=x;", container="c")
    ]

    assert kinds == [
        "SearchIndexerDataSourceConnection",
        "SearchIndex",
        "SearchIndexerSkillset",
        "SearchIndexer",
    ]


def test_bedrock_sidecar_has_filterable_attributes() -> None:
    assert object_key(DOC) == "faq/faq-mexico-city-es.md"
    assert metadata_sidecar(DOC) == {
        "metadataAttributes": {
            "document_id": "faq-mexico-city-es",
            "doc_type": "faq",
            "language": "es",
            "title": "Preguntas frecuentes: Mexico City",
            "city": "Mexico City",
        }
    }


async def test_bedrock_upload_and_sync() -> None:
    s3 = boto3.client(
        "s3", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    agent = boto3.client(
        "bedrock-agent", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    s3_stub, agent_stub = Stubber(s3), Stubber(agent)
    s3_stub.add_response(
        "put_object",
        {},
        {"Bucket": "docs", "Key": object_key(DOC), "Body": ANY, "ContentType": ANY},
    )
    s3_stub.add_response(
        "put_object",
        {},
        {
            "Bucket": "docs",
            "Key": f"{object_key(DOC)}.metadata.json",
            "Body": json.dumps(metadata_sidecar(DOC), ensure_ascii=False).encode(),
            "ContentType": "application/json",
        },
    )
    now = datetime(2026, 10, 7, tzinfo=UTC)
    job = {
        "knowledgeBaseId": "KB1",
        "dataSourceId": "DS1",
        "ingestionJobId": "JOB1",
        "startedAt": now,
        "updatedAt": now,
    }
    agent_stub.add_response(
        "start_ingestion_job",
        {"ingestionJob": {**job, "status": "STARTING"}},
        {"knowledgeBaseId": "KB1", "dataSourceId": "DS1"},
    )
    agent_stub.add_response(
        "get_ingestion_job",
        {
            "ingestionJob": {
                **job,
                "status": "COMPLETE",
                "statistics": {"numberOfDocumentsScanned": 1, "numberOfNewDocumentsIndexed": 1},
            }
        },
        {"knowledgeBaseId": "KB1", "dataSourceId": "DS1", "ingestionJobId": "JOB1"},
    )
    ingestion = BedrockKnowledgeBaseIngestion(
        s3=s3,
        agent=agent,
        bucket="docs",
        knowledge_base_id="KB1",
        data_source_id="DS1",
    )

    with s3_stub, agent_stub:
        await ingestion.upload(DOC)
        job_id = await ingestion.start_sync()
        status = await ingestion.wait(job_id, poll_s=0)

    assert (status.status, status.scanned, status.indexed) == ("COMPLETE", 1, 1)
    s3_stub.assert_no_pending_responses()
    agent_stub.assert_no_pending_responses()


def test_bedrock_sidecar_preserves_explicit_acl_without_granting_default_access() -> None:
    document = KnowledgeDocument(
        id=DOC.id,
        title=DOC.title,
        doc_type=DOC.doc_type,
        language=DOC.language,
        content=DOC.content,
        metadata={"acl": "internal", "event_id": "event-1"},
    )
    metadata = metadata_sidecar(document)["metadataAttributes"]
    assert metadata["acl"] == "internal" and metadata["event_id"] == "event-1"
    assert "acl" not in metadata_sidecar(DOC)["metadataAttributes"]

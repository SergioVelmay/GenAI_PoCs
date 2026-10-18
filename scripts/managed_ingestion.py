"""Carril A (gestionado): sube el corpus y deja que el servicio trocee, vectorice e indexe.

Uso:
    uv run python scripts/managed_ingestion.py azure            # dry-run: definiciones JSON
    uv run python scripts/managed_ingestion.py aws              # dry-run: objetos y metadatos
    uv run python scripts/managed_ingestion.py azure --apply    # sube blobs y lanza el indexer
    uv run python scripts/managed_ingestion.py aws --apply      # sube a S3 y sincroniza la KB

`--apply` usa la nube y tiene coste (embeddings y operaciones): pide confirmación antes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from headache_core.adapters import azure_search_managed as azure_lane
from headache_core.adapters import bedrock_kb as aws_lane
from headache_core.config import Settings, get_settings
from headache_core.domain import DocumentType, KnowledgeDocument

DEFAULT_SOURCE = Path("data/synthetic/knowledge/documents.jsonl")
SECRET_KEYS = {"apiKey", "connectionString"}


def load_corpus(path: Path, limit: int | None) -> list[KnowledgeDocument]:
    docs = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            docs.append(
                KnowledgeDocument(
                    id=row["id"],
                    title=row["title"],
                    doc_type=DocumentType(row["doc_type"]),
                    language=row["language"],
                    content=row["content"],
                    metadata=row.get("metadata", {}),
                )
            )
    return docs[:limit]


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "***" if k in SECRET_KEYS and v else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def azure_inputs(settings: Settings) -> tuple[azure_lane.EmbeddingTarget, str]:
    managed = settings.azure_managed
    openai = settings.azure_openai
    key = openai.api_key.get_secret_value() if openai.api_key else None
    target = azure_lane.EmbeddingTarget(
        resource_url=openai.endpoint or "https://<recurso>.openai.azure.com",
        deployment=openai.embedding_deployment,
        api_key=key or None,
    )
    if managed.storage_connection_string is not None:
        credentials = managed.storage_connection_string.get_secret_value()
    else:
        credentials = f"ResourceId={managed.storage_resource_id or '<id de la cuenta>'};"
    return target, credentials


def azure_dry_run(settings: Settings, docs: list[KnowledgeDocument]) -> None:
    target, credentials = azure_inputs(settings)
    names = azure_lane.ManagedLaneNames(settings.azure_managed.prefix)
    for definition in azure_lane.definitions(
        names, target, credentials=credentials, container=settings.azure_managed.container
    ):
        print(f"--- {type(definition).__name__}")
        print(json.dumps(redact(definition.as_dict()), indent=2, ensure_ascii=False))  # type: ignore[attr-defined]
    for doc in docs[:3]:
        print(f"blob {azure_lane.blob_name(doc)} metadata={azure_lane.blob_metadata(doc)}")
    print(f"... {len(docs)} blobs en total")


async def azure_apply(settings: Settings, docs: list[KnowledgeDocument]) -> None:
    from azure.identity.aio import DefaultAzureCredential
    from azure.search.documents.indexes.aio import SearchIndexClient, SearchIndexerClient
    from azure.storage.blob.aio import BlobServiceClient

    managed = settings.azure_managed
    target, credentials = azure_inputs(settings)
    names = azure_lane.ManagedLaneNames(managed.prefix)
    from azure.core.exceptions import ResourceNotFoundError

    uploaded = 0
    async with DefaultAzureCredential() as credential:
        async with BlobServiceClient(managed.storage_account_url, credential) as blobs:
            container = blobs.get_container_client(managed.container)
            for doc in docs:
                blob = container.get_blob_client(azure_lane.blob_name(doc))
                try:
                    properties = await blob.get_blob_properties()
                    md5, metadata = properties.content_settings.content_md5, properties.metadata
                except ResourceNotFoundError:
                    md5, metadata = None, None
                if not azure_lane.needs_upload(doc, md5, metadata):
                    continue
                await blob.upload_blob(
                    doc.content.encode(), overwrite=True, metadata=azure_lane.blob_metadata(doc)
                )
                uploaded += 1
        endpoint = settings.azure_search.endpoint
        async with (
            SearchIndexClient(endpoint, credential) as indexes,
            SearchIndexerClient(endpoint, credential) as indexers,
        ):
            await indexers.create_or_update_data_source_connection(
                azure_lane.data_source(names, credentials=credentials, container=managed.container)
            )
            await indexes.create_or_update_index(azure_lane.chunk_index(names, target))
            await indexers.create_or_update_skillset(azure_lane.skillset(names, target))
            await indexers.create_or_update_indexer(azure_lane.indexer(names))
            await indexers.run_indexer(names.indexer)
    print(
        f"{uploaded} de {len(docs)} blobs subidos (el resto, sin cambios); "
        f"indexer '{names.indexer}' lanzado."
    )


def aws_dry_run(settings: Settings, docs: list[KnowledgeDocument]) -> None:
    kb = settings.bedrock_kb
    print(f"KB={kb.knowledge_base_id or '<terraform output>'} DS={kb.data_source_id or '<...>'}")
    for doc in docs[:3]:
        key = aws_lane.object_key(doc)
        print(f"s3://{kb.docs_bucket or '<bucket>'}/{key}")
        print(f"  {key}.metadata.json = {json.dumps(aws_lane.metadata_sidecar(doc))}")
    print(f"... {len(docs)} documentos (x2 objetos) en total")


async def aws_apply(settings: Settings, docs: list[KnowledgeDocument]) -> None:
    import boto3

    kb = settings.bedrock_kb
    session = boto3.Session(profile_name=kb.profile or None, region_name=kb.region)
    ingestion = aws_lane.BedrockKnowledgeBaseIngestion(
        s3=session.client("s3"),
        agent=session.client("bedrock-agent"),
        bucket=kb.docs_bucket,
        knowledge_base_id=kb.knowledge_base_id,
        data_source_id=kb.data_source_id,
    )
    for doc in docs:
        await ingestion.upload(doc)
    job_id = await ingestion.start_sync()
    print(f"{len(docs)} documentos subidos; ingestion job {job_id} en curso...")
    print(await ingestion.wait(job_id))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cloud", choices=["azure", "aws"])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    docs = load_corpus(args.source, args.limit)
    if not args.apply:
        (azure_dry_run if args.cloud == "azure" else aws_dry_run)(settings, docs)
        return
    asyncio.run((azure_apply if args.cloud == "azure" else aws_apply)(settings, docs))


if __name__ == "__main__":
    main()

"""Carril A en AWS: Bedrock Knowledge Base que sincroniza desde S3 hacia S3 Vectors.

La KB, su data source (troceo incluido) y el índice vectorial los crea Terraform. El código
solo sube el corpus a S3 y lanza un *ingestion job*; Bedrock trocea, llama a Titan, escribe
los vectores y detecta altas, cambios y borrados respecto a la sincronización anterior.

Metadatos: fichero sidecar `<objeto>.metadata.json` con `metadataAttributes`, filtrables
después en `Retrieve` (fase 4).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from headache_core.domain import KnowledgeDocument

if TYPE_CHECKING:
    from mypy_boto3_bedrock_agent import AgentsforBedrockClient
    from mypy_boto3_s3 import S3Client

FILTERABLE_ATTRIBUTES = ("document_id", "doc_type", "language", "category", "city", "title")
TERMINAL_STATES = {"COMPLETE", "FAILED", "STOPPED"}


def object_key(document: KnowledgeDocument) -> str:
    return f"{document.doc_type.value}/{document.id}.md"


def metadata_sidecar(document: KnowledgeDocument) -> dict[str, dict[str, str]]:
    values = {
        "document_id": document.id,
        "doc_type": document.doc_type.value,
        "language": document.language,
        "title": document.title,
        **document.metadata,
    }
    return {"metadataAttributes": {k: v for k, v in values.items() if k in FILTERABLE_ATTRIBUTES}}


@dataclass(frozen=True, slots=True)
class IngestionStatus:
    job_id: str
    status: str
    scanned: int = 0
    indexed: int = 0
    modified: int = 0
    deleted: int = 0
    failed: int = 0


class BedrockKnowledgeBaseIngestion:
    def __init__(
        self,
        *,
        s3: S3Client,
        agent: AgentsforBedrockClient,
        bucket: str,
        knowledge_base_id: str,
        data_source_id: str,
    ) -> None:
        self._s3 = s3
        self._agent = agent
        self._bucket = bucket
        self._kb = knowledge_base_id
        self._ds = data_source_id

    async def upload(self, document: KnowledgeDocument) -> None:
        key = object_key(document)
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=document.content.encode(),
            ContentType="text/markdown; charset=utf-8",
        )
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self._bucket,
            Key=f"{key}.metadata.json",
            Body=json.dumps(metadata_sidecar(document), ensure_ascii=False).encode(),
            ContentType="application/json",
        )

    async def start_sync(self) -> str:
        response = await asyncio.to_thread(
            self._agent.start_ingestion_job, knowledgeBaseId=self._kb, dataSourceId=self._ds
        )
        return response["ingestionJob"]["ingestionJobId"]

    async def status(self, job_id: str) -> IngestionStatus:
        response = await asyncio.to_thread(
            self._agent.get_ingestion_job,
            knowledgeBaseId=self._kb,
            dataSourceId=self._ds,
            ingestionJobId=job_id,
        )
        job = response["ingestionJob"]
        stats = job.get("statistics", {})
        return IngestionStatus(
            job_id=job_id,
            status=job["status"],
            scanned=stats.get("numberOfDocumentsScanned", 0),
            indexed=stats.get("numberOfNewDocumentsIndexed", 0),
            modified=stats.get("numberOfModifiedDocumentsIndexed", 0),
            deleted=stats.get("numberOfDocumentsDeleted", 0),
            failed=stats.get("numberOfDocumentsFailed", 0),
        )

    async def wait(self, job_id: str, *, poll_s: float = 10.0) -> IngestionStatus:
        while True:
            current = await self.status(job_id)
            if current.status in TERMINAL_STATES:
                return current
            await asyncio.sleep(poll_s)

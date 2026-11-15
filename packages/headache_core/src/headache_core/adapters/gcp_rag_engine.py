"""Carril A en GCP: RAG Engine importa desde Cloud Storage (fase 3.G3).

Como Bedrock Knowledge Bases: el código sube el corpus a un bucket y pide una importación; el
servicio trocea, llama al modelo de embeddings y guarda los vectores (en `RagManagedDb`, un
Spanner gestionado). El bucket, la configuración de RAG Engine y los corpus los crea Terraform
(`lane_a.tf`). Cliente: `agentplatform.Client(...).aio.rag` (`vertexai.rag` está obsoleto).

Tres diferencias con Bedrock KB que el adaptador resuelve:

- **ACL por partición, no por filtro.** El filtro por metadatos de RAG Engine está en preview
  (`v1beta1`, CEL, no disponible en modo *serverless*) y hay informes de que se ignora. Un fallo
  ahí filtraría documentos internos, así que cada nivel de acceso tiene **su corpus**
  (`public`, `internal`) y la ACL se garantiza eligiendo a qué corpus se pregunta (fase 4.G2).
- **Idempotencia de quien publica**: solo se sube un objeto si cambian su MD5 o sus metadatos
  (lección del carril A de Azure: resubir todo hacía reprocesar todo).
- **Los borrados no se sincronizan solos**: quitar un objeto del bucket no borra su `RagFile`.
  `prune` borra del corpus (y del bucket) lo que ya no está en el corpus de origen, también un
  documento que **cambió de ACL**: si no, seguiría respondiendo desde el corpus público.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from headache_core.config import Settings
from headache_core.domain import KnowledgeDocument
from headache_core.domain.retrieval import classify_acl

ACL_LEVELS = ("public", "internal")
# Metadatos del objeto en Cloud Storage: trazabilidad y detección de cambios (no se filtra con
# ellos; RAG Engine no los lee).
OBJECT_METADATA = ("document_id", "doc_type", "language", "acl")


def document_acl(document: KnowledgeDocument) -> str:
    """La ACL explícita del documento o, si falta, la del dominio (cerrada por defecto)."""
    acl = str(document.metadata.get("acl") or classify_acl(document.doc_type.value))
    if acl not in ACL_LEVELS:
        raise ValueError(f"ACL desconocida '{acl}' en {document.id}: no hay corpus para ella.")
    return acl


def object_name(document: KnowledgeDocument) -> str:
    """`<acl>/<tipo>/<id>.md`: cada corpus importa solo el prefijo de su ACL."""
    return f"{document_acl(document)}/{document.doc_type.value}/{document.id}.md"


def object_metadata(document: KnowledgeDocument) -> dict[str, str]:
    return {
        "document_id": document.id,
        "doc_type": document.doc_type.value,
        "language": document.language,
        "acl": document_acl(document),
    }


def content_md5(document: KnowledgeDocument) -> str:
    """MD5 en base64, el formato en que Cloud Storage lo guarda (`Blob.md5_hash`)."""
    return base64.b64encode(hashlib.md5(document.content.encode()).digest()).decode()


def needs_upload(
    document: KnowledgeDocument, md5: str | None, metadata: Mapping[str, str] | None
) -> bool:
    return md5 != content_md5(document) or dict(metadata or {}) != object_metadata(document)


@dataclass(frozen=True, slots=True)
class ImportStatus:
    acl: str
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    pruned: int = 0


class Bucket(Protocol):
    """Lo que el adaptador usa de un bucket de Cloud Storage (síncrono, va a un hilo)."""

    name: str

    def get_blob(self, blob_name: str) -> Any: ...

    def blob(self, blob_name: str) -> Any: ...

    def list_blobs(self, *, prefix: str) -> Any: ...


class RagClient(Protocol):
    """Lo que el adaptador usa de `Client.aio.rag` (permite un doble en los tests)."""

    async def import_files(self, *, name: str, import_config: Any) -> Any: ...

    async def list_files(self, *, name: str, config: Any = None) -> Any: ...

    async def delete_file(self, *, name: str) -> None: ...


class RagEngineIngestion:
    def __init__(
        self,
        *,
        rag: RagClient,
        bucket: Bucket,
        corpora: Mapping[str, str],
        chunk_size: int = 300,
        chunk_overlap: int = 60,
    ) -> None:
        missing = [acl for acl in ACL_LEVELS if not corpora.get(acl)]
        if missing:
            raise ValueError(f"Faltan corpus de RAG Engine para: {', '.join(missing)}.")
        self._rag = rag
        self._bucket = bucket
        self._corpora = dict(corpora)
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    async def upload(self, document: KnowledgeDocument) -> bool:
        """Sube el documento solo si cambió. Devuelve si lo ha subido."""
        name = object_name(document)
        current = await asyncio.to_thread(self._bucket.get_blob, name)
        if current is not None and not needs_upload(document, current.md5_hash, current.metadata):
            return False
        blob = self._bucket.blob(name)
        blob.metadata = object_metadata(document)
        await asyncio.to_thread(
            blob.upload_from_string,
            document.content.encode(),
            content_type="text/markdown; charset=utf-8",
        )
        return True

    async def sync(self, acl: str) -> ImportStatus:
        """Importa el prefijo de una ACL en su corpus. `import_files` espera a la operación."""
        from agentplatform import types
        from google.genai.types import GcsSource

        response = await self._rag.import_files(
            name=self._corpora[acl],
            import_config=types.ImportRagFilesConfig(
                gcs_source=GcsSource(uris=[f"gs://{self._bucket.name}/{acl}/"]),
                rag_file_transformation_config=types.RagFileTransformationConfig(
                    rag_file_chunking_config=types.RagFileChunkingConfig(
                        fixed_length_chunking=types.RagFileChunkingConfigFixedLengthChunking(
                            chunk_size=self._chunk_size, chunk_overlap=self._chunk_overlap
                        )
                    )
                ),
            ),
        )
        return ImportStatus(
            acl=acl,
            imported=response.imported_rag_files_count or 0,
            skipped=response.skipped_rag_files_count or 0,
            failed=response.failed_rag_files_count or 0,
        )

    async def prune(self, acl: str, documents: list[KnowledgeDocument]) -> int:
        """Borra del corpus y del bucket lo que ya no pertenece a esta ACL. Devuelve cuántos."""
        expected = {
            f"gs://{self._bucket.name}/{object_name(doc)}"
            for doc in documents
            if document_acl(doc) == acl
        }
        pruned = 0
        for rag_file in await self._files(acl):
            uris = list(rag_file.gcs_source.uris) if rag_file.gcs_source else []
            if uris and not set(uris) <= expected:
                await self._rag.delete_file(name=rag_file.name)
                pruned += 1
        prefix_uri = f"gs://{self._bucket.name}/"
        blobs = await asyncio.to_thread(lambda: list(self._bucket.list_blobs(prefix=f"{acl}/")))
        for blob in blobs:
            if prefix_uri + blob.name not in expected:
                await asyncio.to_thread(blob.delete)
        return pruned

    async def _files(self, acl: str) -> list[Any]:
        from agentplatform import types

        files: list[Any] = []
        token: str | None = None
        while True:
            response = await self._rag.list_files(
                name=self._corpora[acl],
                config=types.ListRagFilesConfig(page_size=100, page_token=token),
            )
            files.extend(response.rag_files or [])
            token = response.next_page_token
            if not token:
                return files


def build_ingestion(settings: Settings) -> RagEngineIngestion:
    """Composición con la nube real: `Settings` → cliente de `agentplatform` + bucket."""
    import agentplatform
    from google.cloud import storage

    from headache_core.adapters.gcp_auth import gcp_credentials

    gcp, rag = settings.gcp, settings.gcp_rag_engine
    if not (gcp.project and rag.bucket):
        raise ValueError("Faltan GCP__PROJECT y GCP_RAG_ENGINE__BUCKET (salidas de lane_a.tf).")
    credentials = gcp_credentials(gcp)
    client = agentplatform.Client(project=gcp.project, location=gcp.region, credentials=credentials)
    # `google-cloud-storage` no anota el constructor ni `bucket`.
    storage_client = storage.Client(project=gcp.project, credentials=credentials)  # type: ignore[no-untyped-call]
    bucket = storage_client.bucket(rag.bucket)  # type: ignore[no-untyped-call]
    return RagEngineIngestion(
        rag=client.aio.rag,
        bucket=bucket,
        corpora=rag.corpora(),
        chunk_size=rag.chunk_size,
        chunk_overlap=rag.chunk_overlap,
    )

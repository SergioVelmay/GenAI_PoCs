"""Carril A de GCP (3.G3) sin red: RAG Engine con un corpus por ACL, subida idempotente y poda.

Los dobles salen de la documentación y de los tipos reales de `agentplatform`; se sustituyen por
respuestas capturadas en la primera validación en la nube (pendientes-cloud.md, fase 3 · GCP).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from agentplatform import types
from google.genai.types import GcsSource

from headache_core.adapters.gcp_rag_engine import (
    RagEngineIngestion,
    content_md5,
    document_acl,
    needs_upload,
    object_metadata,
    object_name,
)
from headache_core.domain import DocumentType, KnowledgeDocument

CORPORA = {
    "public": "projects/p/locations/europe-west4/ragCorpora/1",
    "internal": "projects/p/locations/europe-west4/ragCorpora/2",
}


def doc(doc_id: str, *, acl: str | None = "public", content: str = "Texto.") -> KnowledgeDocument:
    return KnowledgeDocument(
        id=doc_id,
        title="T",
        doc_type=DocumentType.POLICY,
        language="es",
        content=content,
        metadata={"acl": acl} if acl else {},
    )


@dataclass
class FakeBlob:
    name: str
    bucket: FakeBucket
    md5_hash: str | None = None
    metadata: dict[str, str] | None = None

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        self.bucket.uploads.append(self.name)
        self.md5_hash = content_md5(doc("x", content=data.decode()))
        self.bucket.blobs[self.name] = self

    def delete(self) -> None:
        del self.bucket.blobs[self.name]


@dataclass
class FakeBucket:
    name: str = "headache-dev-rag"
    blobs: dict[str, FakeBlob] = field(default_factory=dict)
    uploads: list[str] = field(default_factory=list)

    def get_blob(self, blob_name: str) -> FakeBlob | None:
        return self.blobs.get(blob_name)

    def blob(self, blob_name: str) -> FakeBlob:
        return FakeBlob(blob_name, self)

    def list_blobs(self, *, prefix: str) -> list[FakeBlob]:
        return [b for name, b in self.blobs.items() if name.startswith(prefix)]


@dataclass
class FakeRag:
    files: dict[str, list[str]] = field(default_factory=dict)  # corpus -> URIs
    imports: list[tuple[str, Any]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    async def import_files(self, *, name: str, import_config: Any) -> Any:
        self.imports.append((name, import_config))
        return types.ImportRagFilesResponse(
            imported_rag_files_count=1, skipped_rag_files_count=2, failed_rag_files_count=0
        )

    async def list_files(self, *, name: str, config: Any = None) -> Any:
        return types.ListRagFilesResponse(
            rag_files=[
                types.RagFile(name=f"{name}/ragFiles/{i}", gcs_source=GcsSource(uris=[uri]))
                for i, uri in enumerate(self.files.get(name, []))
            ]
        )

    async def delete_file(self, *, name: str) -> None:
        self.deleted.append(name)


def make() -> tuple[RagEngineIngestion, FakeRag, FakeBucket]:
    rag, bucket = FakeRag(), FakeBucket()
    return RagEngineIngestion(rag=rag, bucket=bucket, corpora=CORPORA), rag, bucket


def test_acl_comes_from_the_document_or_the_domain_and_unknown_is_rejected() -> None:
    assert document_acl(doc("a", acl="internal")) == "internal"
    # Sin ACL explícita manda el dominio (una política es pública).
    assert document_acl(doc("a", acl=None)) == "public"
    with pytest.raises(ValueError, match="no hay corpus"):
        document_acl(doc("a", acl="partners"))
    assert object_name(doc("a", acl="internal")) == "internal/policy/a.md"


def test_upload_is_needed_only_when_content_or_metadata_change() -> None:
    document = doc("a")
    md5 = content_md5(document)

    assert not needs_upload(document, md5, object_metadata(document))
    assert needs_upload(doc("a", content="Otro texto."), md5, object_metadata(document))
    assert needs_upload(document, md5, {**object_metadata(document), "language": "en"})
    assert needs_upload(document, None, None)


async def test_republishing_unchanged_documents_uploads_nothing() -> None:
    ingestion, _, bucket = make()

    assert await ingestion.upload(doc("a")) is True
    assert await ingestion.upload(doc("a")) is False
    assert await ingestion.upload(doc("a", content="Cambiado.")) is True

    assert bucket.uploads == ["public/policy/a.md", "public/policy/a.md"]


async def test_sync_imports_only_the_prefix_of_its_acl_with_fixed_chunking() -> None:
    ingestion, rag, _ = make()

    status = await ingestion.sync("internal")

    ((corpus, config),) = rag.imports
    assert corpus == CORPORA["internal"]
    assert config.gcs_source.uris == ["gs://headache-dev-rag/internal/"]
    chunking = config.rag_file_transformation_config.rag_file_chunking_config
    assert (
        chunking.fixed_length_chunking.chunk_size,
        chunking.fixed_length_chunking.chunk_overlap,
    ) == (
        300,
        60,
    )
    assert (status.imported, status.skipped, status.failed) == (1, 2, 0)


async def test_a_document_that_becomes_internal_leaves_the_public_corpus() -> None:
    """La razón de podar: sin esto seguiría respondiendo desde el corpus público."""
    ingestion, rag, bucket = make()
    await ingestion.upload(doc("a"))
    await ingestion.upload(doc("b"))
    rag.files[CORPORA["public"]] = [
        "gs://headache-dev-rag/public/policy/a.md",
        "gs://headache-dev-rag/public/policy/b.md",
    ]
    now = [doc("a", acl="internal"), doc("b")]
    await ingestion.upload(now[0])

    pruned = await ingestion.prune("public", now)

    assert pruned == 1
    assert rag.deleted == [f"{CORPORA['public']}/ragFiles/0"]
    assert sorted(bucket.blobs) == ["internal/policy/a.md", "public/policy/b.md"]


def test_every_acl_needs_its_corpus() -> None:
    with pytest.raises(ValueError, match="internal"):
        RagEngineIngestion(rag=FakeRag(), bucket=FakeBucket(), corpora={"public": "c"})

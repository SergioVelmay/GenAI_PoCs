"""Caso de uso: indexar (o retirar) un documento de conocimiento de forma idempotente e incremental.

Orden de escritura pensado para fallos a mitad (entrega at-least-once):
1. upsert de los chunks nuevos (ids deterministas: repetirlo no duplica nada);
2. borrado de los chunks que ya no existen;
3. el registro (ledger) se guarda el último. Si el worker muere antes, el mensaje se
   reentrega y se rehace todo sin efectos duplicados.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from headache_core.application.ingestion.chunking import Chunker
from headache_core.domain import KnowledgeDocument
from headache_core.ports import (
    EmbeddingPort,
    IngestionLedgerPort,
    LedgerEntry,
    MetadataValue,
    VectorRecord,
    VectorStorePort,
)


class IndexingOutcome(StrEnum):
    INDEXED = "indexed"
    UNCHANGED = "unchanged"
    DELETED = "deleted"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class IndexingResult:
    document_id: str
    outcome: IndexingOutcome
    chunks_upserted: int = 0
    chunks_deleted: int = 0


def content_hash(document: KnowledgeDocument) -> str:
    canonical = json.dumps(
        {
            "title": document.title,
            "doc_type": document.doc_type.value,
            "language": document.language,
            "content": document.content,
            "metadata": document.metadata,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def chunk_id(document_id: str, ordinal: int) -> str:
    return f"{document_id}::{ordinal:04d}"


class DocumentIndexer:
    def __init__(
        self,
        *,
        index_name: str,
        chunker: Chunker,
        embeddings: EmbeddingPort,
        store: VectorStorePort,
        ledger: IngestionLedgerPort,
        pipeline_version: str,
        embed_batch_size: int = 16,
    ) -> None:
        self._index_name = index_name
        self._chunker = chunker
        self._embeddings = embeddings
        self._store = store
        self._ledger = ledger
        self._pipeline_version = pipeline_version
        self._batch_size = embed_batch_size

    async def index(self, document: KnowledgeDocument) -> IndexingResult:
        digest = content_hash(document)
        previous = await self._ledger.get(self._index_name, document.id)
        if (
            previous is not None
            and previous.content_hash == digest
            and previous.pipeline_version == self._pipeline_version
        ):
            # Deduplicación: ni embeddings (coste) ni escrituras si nada ha cambiado.
            return IndexingResult(document.id, IndexingOutcome.UNCHANGED)

        pieces = self._chunker.split(document.content)
        records = await self._embed(document, digest, pieces)
        await self._store.upsert(records)

        new_ids = {r.id for r in records}
        stale = sorted(set(previous.chunk_ids) - new_ids) if previous else []
        if stale:
            await self._store.delete(stale)

        await self._ledger.save(
            LedgerEntry(
                index_name=self._index_name,
                document_id=document.id,
                content_hash=digest,
                chunk_ids=tuple(r.id for r in records),
                pipeline_version=self._pipeline_version,
            )
        )
        return IndexingResult(document.id, IndexingOutcome.INDEXED, len(records), len(stale))

    async def remove(self, document_id: str) -> IndexingResult:
        previous = await self._ledger.get(self._index_name, document_id)
        if previous is None:
            return IndexingResult(document_id, IndexingOutcome.NOT_FOUND)
        await self._store.delete(previous.chunk_ids)
        await self._ledger.delete(self._index_name, document_id)
        return IndexingResult(
            document_id, IndexingOutcome.DELETED, chunks_deleted=len(previous.chunk_ids)
        )

    async def _embed(
        self, document: KnowledgeDocument, digest: str, pieces: list[str]
    ) -> list[VectorRecord]:
        # Cabecera contextual: el título da contexto a chunks que solos serían ambiguos
        # ("Reembolsable: sí, hasta 48 horas"... ¿de qué categoría?).
        inputs = [f"{document.title}\n\n{piece}" for piece in pieces]
        vectors: list[list[float]] = []
        for start in range(0, len(inputs), self._batch_size):
            vectors.extend(
                await self._embeddings.embed_texts(inputs[start : start + self._batch_size])
            )

        base: dict[str, MetadataValue] = {
            **document.metadata,
            "document_id": document.id,
            "title": document.title,
            "doc_type": document.doc_type.value,
            "language": document.language,
            "content_hash": digest,
        }
        return [
            VectorRecord(
                id=chunk_id(document.id, ordinal),
                text=piece,
                embedding=vector,
                metadata={**base, "chunk": ordinal},
            )
            for ordinal, (piece, vector) in enumerate(zip(pieces, vectors, strict=True))
        ]

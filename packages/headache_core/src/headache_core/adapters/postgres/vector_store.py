"""pgvector como `VectorStorePort` y registro de ingesta en PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from headache_core.adapters.postgres.tables import IngestionLedgerRow, KnowledgeChunkRow
from headache_core.ports import LedgerEntry, MetadataValue, SearchResult, VectorRecord


class PgVectorStore:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, index_name: str
    ) -> None:
        self._session_factory = session_factory
        self._index_name = index_name

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        stmt = insert(KnowledgeChunkRow).values(
            [
                {
                    "index_name": self._index_name,
                    "id": r.id,
                    "text": r.text,
                    "embedding": r.embedding,
                    "attributes": r.metadata,
                }
                for r in records
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[KnowledgeChunkRow.index_name, KnowledgeChunkRow.id],
            set_={
                "text": stmt.excluded.text,
                "embedding": stmt.excluded.embedding,
                "attributes": stmt.excluded.attributes,
                "updated_at": text("now()"),
            },
        )
        async with self._session_factory() as session, session.begin():
            await session.execute(stmt)

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, MetadataValue] | None = None,
    ) -> list[SearchResult]:
        distance = KnowledgeChunkRow.embedding.cosine_distance(list(query_embedding))
        stmt = (
            select(KnowledgeChunkRow, distance.label("distance"))
            .where(KnowledgeChunkRow.index_name == self._index_name)
            .order_by(distance)
            .limit(top_k)
        )
        if filters:
            stmt = stmt.where(KnowledgeChunkRow.attributes.contains(dict(filters)))
        async with self._session_factory() as session, session.begin():
            # pgvector >= 0.8: si el filtro descarta candidatos, HNSW sigue buscando en vez de
            # devolver menos de top_k resultados (el clásico problema del post-filtering).
            await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
            rows = (await session.execute(stmt)).all()
        return [
            SearchResult(
                VectorRecord(
                    id=row.id,
                    text=row.text,
                    embedding=list(row.embedding),
                    metadata=dict(row.attributes),
                ),
                score=1.0 - float(dist),
            )
            for row, dist in rows
        ]

    async def delete(self, ids: Sequence[str]) -> None:
        if not ids:
            return
        async with self._session_factory() as session, session.begin():
            await session.execute(
                delete(KnowledgeChunkRow).where(
                    KnowledgeChunkRow.index_name == self._index_name,
                    KnowledgeChunkRow.id.in_(list(ids)),
                )
            )


class PostgresIngestionLedger:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, index_name: str, document_id: str) -> LedgerEntry | None:
        async with self._session_factory() as session:
            row = await session.get(IngestionLedgerRow, (index_name, document_id))
        if row is None:
            return None
        return LedgerEntry(
            index_name=row.index_name,
            document_id=row.document_id,
            content_hash=row.content_hash,
            chunk_ids=tuple(row.chunk_ids),
            pipeline_version=row.pipeline_version,
        )

    async def save(self, entry: LedgerEntry) -> None:
        values = {
            "index_name": entry.index_name,
            "document_id": entry.document_id,
            "content_hash": entry.content_hash,
            "chunk_ids": list(entry.chunk_ids),
            "pipeline_version": entry.pipeline_version,
        }
        stmt = insert(IngestionLedgerRow).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[IngestionLedgerRow.index_name, IngestionLedgerRow.document_id],
            set_={**values, "indexed_at": text("now()")},
        )
        async with self._session_factory() as session, session.begin():
            await session.execute(stmt)

    async def delete(self, index_name: str, document_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                delete(IngestionLedgerRow).where(
                    IngestionLedgerRow.index_name == index_name,
                    IngestionLedgerRow.document_id == document_id,
                )
            )

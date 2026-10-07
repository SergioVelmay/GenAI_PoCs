"""pgvector + registro de ingesta contra PostgreSQL real (requiere la migración 0003)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from headache_core.adapters.memory import HashingEmbedding
from headache_core.adapters.postgres import (
    EMBEDDING_DIMENSIONS,
    PgVectorStore,
    PostgresIngestionLedger,
    create_engine,
    create_session_factory,
)
from headache_core.adapters.postgres.tables import IngestionLedgerRow, KnowledgeChunkRow
from headache_core.config import get_settings
from headache_core.ports import LedgerEntry, VectorRecord

pytestmark = pytest.mark.integration


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(get_settings().postgres, application_name="integration-tests")
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def index_name(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[str]:
    name = f"it-{uuid.uuid4().hex[:12]}"
    yield name
    async with factory() as session, session.begin():
        await session.execute(delete(KnowledgeChunkRow).where(KnowledgeChunkRow.index_name == name))
        await session.execute(
            delete(IngestionLedgerRow).where(IngestionLedgerRow.index_name == name)
        )


async def test_upsert_search_filter_and_delete(
    factory: async_sessionmaker[AsyncSession], index_name: str
) -> None:
    embeddings = HashingEmbedding(EMBEDDING_DIMENSIONS)
    store = PgVectorStore(factory, index_name=index_name)
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
    await store.upsert(records)  # Idempotente: ON CONFLICT DO UPDATE.

    query = await embeddings.embed_query("candlelit refund")
    top = await store.search(query, top_k=3)
    filtered = await store.search(query, top_k=3, filters={"language": "es"})

    assert top[0].record.id == "refund-en"
    assert top[0].score > top[-1].score
    assert len(top[0].record.embedding) == EMBEDDING_DIMENSIONS
    assert [r.record.id for r in filtered] == ["refund-es"]

    await store.delete(["refund-en", "refund-es"])
    assert [r.record.id for r in await store.search(query, top_k=3)] == ["doors-en"]


async def test_ledger_roundtrip(factory: async_sessionmaker[AsyncSession], index_name: str) -> None:
    ledger = PostgresIngestionLedger(factory)
    entry = LedgerEntry(index_name, "doc-1", "a" * 64, ("doc-1::0000", "doc-1::0001"), "v1")

    await ledger.save(entry)
    await ledger.save(entry)

    assert await ledger.get(index_name, "doc-1") == entry
    await ledger.delete(index_name, "doc-1")
    assert await ledger.get(index_name, "doc-1") is None

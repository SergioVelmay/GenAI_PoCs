"""Carril C contra PostgreSQL real: PGVectorStore + docstore + caché en el esquema `llamaindex`."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from headache_core.adapters.llamaindex import (
    LlamaIndexIndexer,
    PortEmbedding,
    build_postgres_indexer,
)
from headache_core.adapters.memory import HashingEmbedding
from headache_core.adapters.postgres import EMBEDDING_DIMENSIONS, create_engine
from headache_core.application.ingestion import IndexingOutcome
from headache_core.config import get_settings
from headache_core.domain import DocumentType, KnowledgeDocument

pytestmark = pytest.mark.integration


@pytest.fixture
async def table() -> AsyncIterator[str]:
    name = f"it_{uuid.uuid4().hex[:12]}"
    yield name
    engine = create_engine(get_settings().postgres, application_name="integration-tests")
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS llamaindex.data_{name}"))
        for kv in ("data_docstore", "data_ingestion_cache"):
            await conn.execute(
                text(f"DELETE FROM llamaindex.{kv} WHERE namespace LIKE :ns"),
                {"ns": f"{name}%"},
            )
    await engine.dispose()


@pytest.fixture
async def indexer(table: str) -> AsyncIterator[LlamaIndexIndexer]:
    indexer = build_postgres_indexer(
        get_settings().postgres,
        embed_model=PortEmbedding(HashingEmbedding(EMBEDDING_DIMENSIONS), model_name="hashing"),
        embed_dim=EMBEDDING_DIMENSIONS,
        table=table,
        strategy="sentence",
        chunk_size=64,
    )
    yield indexer
    await indexer.aclose()


def _doc(doc_id: str, language: str, content: str) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=doc_id,
        title=f"Policy {doc_id}",
        doc_type=DocumentType.POLICY,
        language=language,
        content=content,
        metadata={"category": "candlelit"},
    )


async def test_index_search_dedup_and_remove(indexer: LlamaIndexIndexer) -> None:
    english = _doc("refund-en", "en", "Candlelit refund policy: refundable up to 48 hours.")
    spanish = _doc("refund-es", "es", "Política de reembolso candlelit: hasta 48 horas.")

    assert (await indexer.index(english)).outcome is IndexingOutcome.INDEXED
    assert (await indexer.index(spanish)).outcome is IndexingOutcome.INDEXED
    assert (await indexer.index(english)).outcome is IndexingOutcome.UNCHANGED

    hits = await indexer.search("candlelit refund", top_k=5, filters={"language": "es"})
    assert [h.record.metadata["document_id"] for h in hits] == ["refund-es"]

    assert (await indexer.remove("refund-es")).outcome is IndexingOutcome.DELETED
    assert await indexer.search("candlelit refund", top_k=5, filters={"language": "es"}) == []

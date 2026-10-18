from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest

from headache_core.adapters.memory import (
    HashingEmbedding,
    InMemoryIngestionLedger,
    InMemoryVectorStore,
)
from headache_core.application.ingestion import (
    DocumentIndexer,
    FixedSizeChunker,
    IndexingOutcome,
    chunk_id,
)
from headache_core.domain import DocumentType, KnowledgeDocument

INDEX = "test-index"


class CountingEmbedding(HashingEmbedding):
    def __init__(self) -> None:
        super().__init__(dimensions=64)
        self.embedded = 0

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.embedded += len(texts)
        return await super().embed_texts(texts)


@pytest.fixture
def document() -> KnowledgeDocument:
    return KnowledgeDocument(
        id="policy-candlelit-en",
        title="Refund policy: Candlelit concerts",
        doc_type=DocumentType.POLICY,
        language="en",
        content=" ".join(f"word{i}" for i in range(25)),
        metadata={"category": "candlelit"},
    )


@pytest.fixture
def embeddings() -> CountingEmbedding:
    return CountingEmbedding()


@pytest.fixture
def store() -> InMemoryVectorStore:
    return InMemoryVectorStore()


@pytest.fixture
def ledger() -> InMemoryIngestionLedger:
    return InMemoryIngestionLedger()


def _indexer(
    embeddings: CountingEmbedding,
    store: InMemoryVectorStore,
    ledger: InMemoryIngestionLedger,
    *,
    version: str = "v1",
) -> DocumentIndexer:
    return DocumentIndexer(
        index_name=INDEX,
        chunker=FixedSizeChunker(max_words=10, overlap_words=0),
        embeddings=embeddings,
        store=store,
        ledger=ledger,
        pipeline_version=version,
        embed_batch_size=2,
    )


async def test_indexes_chunks_with_enriched_metadata(
    document: KnowledgeDocument,
    embeddings: CountingEmbedding,
    store: InMemoryVectorStore,
    ledger: InMemoryIngestionLedger,
) -> None:
    result = await _indexer(embeddings, store, ledger).index(document)

    assert result.outcome is IndexingOutcome.INDEXED
    assert result.chunks_upserted == 3
    assert len(store) == 3
    hits = await store.search(await embeddings.embed_query("word0"), top_k=1)
    assert hits[0].record.id == chunk_id(document.id, 0)
    assert hits[0].record.metadata["category"] == "candlelit"
    assert hits[0].record.metadata["doc_type"] == "policy"
    assert hits[0].record.metadata["chunk"] == 0


async def test_redelivery_of_same_document_is_a_free_no_op(
    document: KnowledgeDocument,
    embeddings: CountingEmbedding,
    store: InMemoryVectorStore,
    ledger: InMemoryIngestionLedger,
) -> None:
    indexer = _indexer(embeddings, store, ledger)
    await indexer.index(document)
    embedded_before = embeddings.embedded

    result = await indexer.index(document)

    assert result.outcome is IndexingOutcome.UNCHANGED
    assert embeddings.embedded == embedded_before
    assert len(store) == 3


async def test_recreated_empty_destination_is_reindexed_after_forgetting_the_ledger(
    document: KnowledgeDocument,
    embeddings: CountingEmbedding,
    ledger: InMemoryIngestionLedger,
) -> None:
    await _indexer(embeddings, InMemoryVectorStore(), ledger).index(document)
    # AI Search efímero: el índice se destruye y se recrea vacío; el ledger aún lo da por hecho.
    recreated = InMemoryVectorStore()
    stale = await _indexer(embeddings, recreated, ledger).index(document)
    assert stale.outcome is IndexingOutcome.UNCHANGED and len(recreated) == 0

    assert await ledger.forget_index(INDEX) == 1
    result = await _indexer(embeddings, recreated, ledger).index(document)

    assert result.outcome is IndexingOutcome.INDEXED and len(recreated) == 3


async def test_shorter_new_version_removes_stale_chunks(
    document: KnowledgeDocument,
    embeddings: CountingEmbedding,
    store: InMemoryVectorStore,
    ledger: InMemoryIngestionLedger,
) -> None:
    indexer = _indexer(embeddings, store, ledger)
    await indexer.index(document)

    result = await indexer.index(replace(document, content="only five words remain here"))

    assert (result.chunks_upserted, result.chunks_deleted) == (1, 2)
    assert len(store) == 1
    entry = await ledger.get(INDEX, document.id)
    assert entry is not None
    assert entry.chunk_ids == (chunk_id(document.id, 0),)


async def test_pipeline_change_forces_reindex(
    document: KnowledgeDocument,
    embeddings: CountingEmbedding,
    store: InMemoryVectorStore,
    ledger: InMemoryIngestionLedger,
) -> None:
    await _indexer(embeddings, store, ledger, version="v1").index(document)

    result = await _indexer(embeddings, store, ledger, version="v2").index(document)

    assert result.outcome is IndexingOutcome.INDEXED


async def test_remove_deletes_chunks_and_ledger(
    document: KnowledgeDocument,
    embeddings: CountingEmbedding,
    store: InMemoryVectorStore,
    ledger: InMemoryIngestionLedger,
) -> None:
    indexer = _indexer(embeddings, store, ledger)
    await indexer.index(document)

    removed = await indexer.remove(document.id)
    again = await indexer.remove(document.id)

    assert (removed.outcome, removed.chunks_deleted) == (IndexingOutcome.DELETED, 3)
    assert again.outcome is IndexingOutcome.NOT_FOUND
    assert len(store) == 0
    assert await ledger.get(INDEX, document.id) is None

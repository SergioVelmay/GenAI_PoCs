"""Composition root del indexer: aquí se eligen los adaptadores concretos."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from headache_core.adapters.memory import HashingEmbedding
from headache_core.adapters.postgres import (
    EMBEDDING_DIMENSIONS,
    PgVectorStore,
    PostgresIngestionLedger,
    create_engine,
    create_session_factory,
)
from headache_core.application.ingestion import DocumentIndexer
from headache_core.config import Settings
from headache_core.ports import EmbeddingPort, VectorStorePort
from indexer.settings import IndexerSettings


@dataclass(frozen=True)
class Pipeline:
    indexer: DocumentIndexer
    store: VectorStorePort
    embeddings: EmbeddingPort


@asynccontextmanager
async def open_pipeline(settings: Settings, config: IndexerSettings) -> AsyncIterator[Pipeline]:
    engine = create_engine(settings.postgres, application_name="indexer")
    try:
        factory = create_session_factory(engine)
        chunker = config.build_chunker()
        # Embeddings locales sin coste; los de Azure OpenAI y Bedrock llegan en el paso 3.2.
        embeddings = HashingEmbedding(EMBEDDING_DIMENSIONS)
        store = PgVectorStore(factory, index_name=config.index_name)
        indexer = DocumentIndexer(
            index_name=config.index_name,
            chunker=chunker,
            embeddings=embeddings,
            store=store,
            ledger=PostgresIngestionLedger(factory),
            pipeline_version=f"{chunker.name}|hashing-{EMBEDDING_DIMENSIONS}",
        )
        yield Pipeline(indexer, store, embeddings)
    finally:
        await engine.dispose()

"""Composition root del indexer: aquí se eligen los adaptadores concretos."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from headache_core.adapters.memory import HashingEmbedding
from headache_core.adapters.postgres import (
    EMBEDDING_DIMENSIONS,
    PgVectorStore,
    PostgresIngestionLedger,
    create_engine,
    create_session_factory,
)
from headache_core.application.ingestion import DocumentIndexer, KnowledgeIndexer
from headache_core.config import Settings
from headache_core.ports import EmbeddingPort, MetadataValue, SearchResult, VectorStorePort
from indexer.settings import IndexerSettings

# Embeddings locales sin coste; los de Azure OpenAI y Bedrock se validan en la nube.
EMBEDDING_MODEL = f"hashing-{EMBEDDING_DIMENSIONS}"


class Searcher(Protocol):
    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, MetadataValue] | None = None,
    ) -> list[SearchResult]: ...


@dataclass(frozen=True)
class NativeSearcher:
    embeddings: EmbeddingPort
    store: VectorStorePort

    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, MetadataValue] | None = None,
    ) -> list[SearchResult]:
        vector = await self.embeddings.embed_query(query)
        return await self.store.search(vector, top_k=top_k, filters=filters)


@dataclass(frozen=True)
class Pipeline:
    indexer: KnowledgeIndexer
    searcher: Searcher


@asynccontextmanager
async def open_pipeline(settings: Settings, config: IndexerSettings) -> AsyncIterator[Pipeline]:
    embeddings = HashingEmbedding(EMBEDDING_DIMENSIONS)
    if config.engine == "llamaindex":
        from headache_core.adapters.llamaindex import PortEmbedding, build_postgres_indexer

        llama = build_postgres_indexer(
            settings.postgres,
            embed_model=PortEmbedding(embeddings, model_name=EMBEDDING_MODEL),
            embed_dim=EMBEDDING_DIMENSIONS,
            table=config.llamaindex_table,
            strategy=config.chunk_strategy,
            chunk_size=config.chunk_size_tokens,
            chunk_overlap=config.chunk_overlap_tokens,
        )
        try:
            yield Pipeline(llama, llama)
        finally:
            await llama.aclose()
        return

    engine = create_engine(settings.postgres, application_name="indexer")
    try:
        factory = create_session_factory(engine)
        chunker = config.build_chunker()
        store = PgVectorStore(factory, index_name=config.index_name)
        indexer = DocumentIndexer(
            index_name=config.index_name,
            chunker=chunker,
            embeddings=embeddings,
            store=store,
            ledger=PostgresIngestionLedger(factory),
            pipeline_version=f"{chunker.name}|{EMBEDDING_MODEL}",
        )
        yield Pipeline(indexer, NativeSearcher(embeddings, store))
    finally:
        await engine.dispose()

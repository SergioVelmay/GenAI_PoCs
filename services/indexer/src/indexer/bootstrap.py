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


def build_embeddings(settings: Settings, config: IndexerSettings) -> tuple[EmbeddingPort, str]:
    """El nombre del modelo entra en `pipeline_version`: cambiarlo fuerza a reindexar."""
    match config.embeddings:
        case "hashing":
            return HashingEmbedding(EMBEDDING_DIMENSIONS), f"hashing-{EMBEDDING_DIMENSIONS}"
        case "azure":
            from headache_core.adapters.cloud_embeddings import AzureOpenAIEmbedding

            azure = settings.azure_openai
            return (
                AzureOpenAIEmbedding.from_settings(azure, EMBEDDING_DIMENSIONS),
                f"azure:{azure.embedding_deployment}-{EMBEDDING_DIMENSIONS}",
            )
        case "bedrock":
            from headache_core.adapters.cloud_embeddings import BedrockTitanEmbedding

            bedrock = settings.bedrock
            return (
                BedrockTitanEmbedding.from_settings(bedrock, EMBEDDING_DIMENSIONS),
                f"bedrock:{bedrock.embedding_model_id}-{EMBEDDING_DIMENSIONS}",
            )


def build_cloud_store(settings: Settings, config: IndexerSettings) -> VectorStorePort:
    if config.vector_store == "azure_search":
        from headache_core.adapters.azure_search import AzureAISearchVectorStore

        return AzureAISearchVectorStore.from_settings(settings.azure_search)
    from headache_core.adapters.s3_vectors import S3VectorStore

    return S3VectorStore.from_settings(settings.s3_vectors)


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


def cloud_ledger_index(index_name: str, vector_store: str) -> str:
    """Un registro por destino: indexado en pgvector no implica indexado en la nube."""
    return f"{index_name}@{vector_store}"


@asynccontextmanager
async def open_pipeline(settings: Settings, config: IndexerSettings) -> AsyncIterator[Pipeline]:
    embeddings, model_name = build_embeddings(settings, config)
    if config.engine == "llamaindex":
        from headache_core.adapters.llamaindex import PortEmbedding, build_postgres_indexer

        llama = build_postgres_indexer(
            settings.postgres,
            embed_model=PortEmbedding(embeddings, model_name=model_name),
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
    store: VectorStorePort | None = None
    try:
        factory = create_session_factory(engine)
        chunker = config.build_chunker()
        if config.vector_store == "pgvector":
            store = PgVectorStore(factory, index_name=config.index_name)
            ledger_index = config.index_name
        else:
            store = build_cloud_store(settings, config)
            ledger_index = cloud_ledger_index(config.index_name, config.vector_store)
        indexer = DocumentIndexer(
            index_name=ledger_index,
            chunker=chunker,
            embeddings=embeddings,
            store=store,
            ledger=PostgresIngestionLedger(factory),
            pipeline_version=f"{chunker.name}|{model_name}",
        )
        yield Pipeline(indexer, NativeSearcher(embeddings, store))
    finally:
        # Los almacenes de la nube abren clientes y credenciales asíncronos que hay que cerrar.
        close = getattr(store, "close", None)
        if close is not None:
            await close()
        await engine.dispose()

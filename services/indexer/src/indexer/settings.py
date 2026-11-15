"""Configuración del indexer (variables `INDEXER__*`)."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from headache_core.application.ingestion import Chunker, FixedSizeChunker, SentenceChunker


class IndexerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INDEXER__",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # native = carril B (código propio); llamaindex = carril C (framework).
    engine: Literal["native", "llamaindex"] = "native"
    # rabbitmq en local; sqs (ElasticMQ/AWS), servicebus (emulador/Azure) y pubsub (emulador/GCP).
    broker: Literal["rabbitmq", "sqs", "servicebus", "pubsub"] = "rabbitmq"
    # hashing = local sin coste; azure (text-embedding-3-small), bedrock (Titan v2) y gcp
    # (gemini-embedding-001) cuestan.
    embeddings: Literal["hashing", "azure", "bedrock", "gcp"] = "hashing"
    # Carril B en la nube: azure_search, s3_vectors o gcp_vector_search (Agent Retrieval). El
    # ledger sigue en PostgreSQL.
    vector_store: Literal["pgvector", "azure_search", "s3_vectors", "gcp_vector_search"] = (
        "pgvector"
    )
    index_name: str = "knowledge"
    topic: str = "knowledge.documents"
    chunk_strategy: Literal["sentence", "fixed", "semantic", "hierarchical"] = "sentence"
    # Carril nativo: en palabras.
    chunk_max_words: int = 80
    chunk_overlap_words: int = 15
    # Carril LlamaIndex: en tokens (tiktoken).
    chunk_size_tokens: int = 128
    chunk_overlap_tokens: int = 20
    # RabbitMQ: forman parte de la declaración de la cola (productor y workers deben coincidir).
    # SQS, Service Bus y Pub/Sub: el máximo de entregas lo fija la infraestructura, no la app.
    max_deliveries: int = 5
    retry_delay_ms: int = 1_000
    prefetch: int = 8

    @model_validator(mode="after")
    def _strategy_supported(self) -> Self:
        if self.engine == "native" and self.chunk_strategy in ("semantic", "hierarchical"):
            raise ValueError(
                f"La estrategia '{self.chunk_strategy}' solo existe en INDEXER__ENGINE=llamaindex."
            )
        if self.engine == "llamaindex" and self.vector_store != "pgvector":
            raise ValueError("El carril LlamaIndex usa su propio PGVectorStore (pgvector).")
        return self

    @property
    def llamaindex_table(self) -> str:
        return f"{self.index_name}_{self.chunk_strategy}"

    def build_chunker(self) -> Chunker:
        if self.chunk_strategy == "fixed":
            return FixedSizeChunker(self.chunk_max_words, self.chunk_overlap_words)
        return SentenceChunker(self.chunk_max_words, self.chunk_overlap_words)

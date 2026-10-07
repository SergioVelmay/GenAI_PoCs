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
    index_name: str = "knowledge"
    topic: str = "knowledge.documents"
    chunk_strategy: Literal["sentence", "fixed", "semantic", "hierarchical"] = "sentence"
    # Carril nativo: en palabras.
    chunk_max_words: int = 80
    chunk_overlap_words: int = 15
    # Carril LlamaIndex: en tokens (tiktoken).
    chunk_size_tokens: int = 128
    chunk_overlap_tokens: int = 20
    # Productor y workers deben coincidir: forman parte de la declaración de la cola.
    max_deliveries: int = 5
    retry_delay_ms: int = 1_000
    prefetch: int = 8

    @model_validator(mode="after")
    def _strategy_supported(self) -> Self:
        if self.engine == "native" and self.chunk_strategy in ("semantic", "hierarchical"):
            raise ValueError(
                f"La estrategia '{self.chunk_strategy}' solo existe en INDEXER__ENGINE=llamaindex."
            )
        return self

    @property
    def llamaindex_table(self) -> str:
        return f"{self.index_name}_{self.chunk_strategy}"

    def build_chunker(self) -> Chunker:
        if self.chunk_strategy == "fixed":
            return FixedSizeChunker(self.chunk_max_words, self.chunk_overlap_words)
        return SentenceChunker(self.chunk_max_words, self.chunk_overlap_words)

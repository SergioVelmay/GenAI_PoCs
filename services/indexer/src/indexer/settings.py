"""Configuración del indexer (variables `INDEXER__*`)."""

from __future__ import annotations

from typing import Literal

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

    index_name: str = "knowledge"
    topic: str = "knowledge.documents"
    chunk_strategy: Literal["sentence", "fixed"] = "sentence"
    chunk_max_words: int = 80
    chunk_overlap_words: int = 15
    # Productor y workers deben coincidir: forman parte de la declaración de la cola.
    max_deliveries: int = 5
    retry_delay_ms: int = 1_000
    prefetch: int = 8

    def build_chunker(self) -> Chunker:
        if self.chunk_strategy == "fixed":
            return FixedSizeChunker(self.chunk_max_words, self.chunk_overlap_words)
        return SentenceChunker(self.chunk_max_words, self.chunk_overlap_words)

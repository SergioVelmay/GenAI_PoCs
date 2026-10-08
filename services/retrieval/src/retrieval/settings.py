"""Configuracion local: ninguna opcion activa modelos cloud."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RetrievalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RETRIEVAL__",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )
    engine: Literal["native", "llamaindex"] = "native"
    merge_ratio: float = Field(default=0.5, gt=0, lt=1)
    source: Literal["synthetic", "postgres"] = "synthetic"
    documents_path: Path = Path("data/synthetic/knowledge/documents.jsonl")
    max_documents: int = Field(default=500, ge=1, le=100_000)
    index_name: str = "knowledge"
    chunk_max_words: int = Field(default=80, ge=10, le=1000)
    chunk_overlap_words: int = Field(default=15, ge=0)

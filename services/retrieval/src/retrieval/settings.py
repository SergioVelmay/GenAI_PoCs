"""Configuracion del servicio: por defecto todo local; la nube solo se activa de forma explicita."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
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
    # Composicion cloud (carril B/A). Cada opcion distinta del valor por defecto tiene coste.
    backend: Literal["local", "azure_search"] = "local"
    embeddings: Literal["hashing", "azure", "bedrock"] = "hashing"
    semantic_ranker: bool = False
    reranker: Literal["token-coverage", "bedrock"] = "token-coverage"
    llm: Literal["none", "gateway"] = "none"
    managed: Literal["none", "bedrock_kb", "foundry_iq"] = "none"

    @model_validator(mode="after")
    def validate_composition(self) -> Self:
        if self.backend == "local" and (self.embeddings != "hashing" or self.semantic_ranker):
            raise ValueError("El backend local solo usa hashing y no tiene Semantic Ranker")
        if self.backend == "azure_search" and self.embeddings == "hashing":
            raise ValueError("AI Search necesita el mismo encoder cloud que la ingesta")
        return self

    @property
    def uses_cloud(self) -> bool:
        return (
            self.backend != "local"
            or self.reranker != "token-coverage"
            or self.llm != "none"
            or self.managed != "none"
        )

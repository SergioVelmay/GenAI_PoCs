"""Puertos de embeddings y almacén vectorial (AI Search, OpenSearch, pgvector, S3 Vectors...)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

type MetadataValue = str | int | float | bool


class EmbeddingPort(Protocol):
    @property
    def dimensions(self) -> int: ...

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True, slots=True)
class VectorRecord:
    id: str
    text: str
    embedding: list[float]
    metadata: dict[str, MetadataValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchResult:
    record: VectorRecord
    score: float


class VectorStorePort(Protocol):
    async def upsert(self, records: Sequence[VectorRecord]) -> None: ...

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, MetadataValue] | None = None,
    ) -> list[SearchResult]: ...

    async def delete(self, ids: Sequence[str]) -> None: ...

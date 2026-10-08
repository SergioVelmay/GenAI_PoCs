"""Puertos para candidatos, expansion de padres y reranking."""

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

from headache_core.domain.retrieval import AccessScope
from headache_core.ports.vector_store import MetadataValue, SearchResult, VectorRecord

type SearchMode = Literal["bm25", "vector", "hybrid", "auto-merge"]
type RetrievalStrategy = Literal[
    "bm25",
    "vector",
    "hybrid",
    "rerank",
    "rewrite",
    "hyde",
    "subquestions",
    "router",
    "auto",
    "parent-child",
    "auto-merge",
]


class RetrievalPort(Protocol):
    async def search(
        self,
        query: str,
        *,
        mode: SearchMode,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
    ) -> list[SearchResult]: ...

    async def get(self, record_id: str, *, scope: AccessScope) -> VectorRecord | None: ...


class RerankerPort(Protocol):
    async def rerank(
        self, query: str, candidates: Sequence[SearchResult], *, top_k: int
    ) -> list[SearchResult]: ...

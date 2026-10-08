"""BM25 real y vectores en memoria: baseline local de solo lectura."""

import re
import unicodedata
from collections.abc import Mapping, Sequence

import numpy as np
from rank_bm25 import BM25Plus

from headache_core.application.retrieval import reciprocal_rank_fusion
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.retrieval import SearchMode
from headache_core.ports.vector_store import (
    EmbeddingPort,
    MetadataValue,
    SearchResult,
    VectorRecord,
)


def tokenize(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return re.findall(
        r"\w+", "".join(char for char in normalized if not unicodedata.combining(char))
    )


class LocalRetrieval:
    def __init__(
        self,
        records: Sequence[VectorRecord],
        *,
        embeddings: EmbeddingPort,
        parents: Sequence[VectorRecord] = (),
    ) -> None:
        self._records = tuple(records)
        self._embeddings = embeddings
        self._sources = {record.id: record for record in (*records, *parents)}
        if len(self._sources) != len(records) + len(parents):
            raise ValueError("IDs de fuentes duplicados")
        if any(len(record.embedding) != embeddings.dimensions for record in records):
            raise ValueError("Dimensiones incompatibles con el modelo de embeddings")
        self._tokens = [tokenize(record.text) for record in records]
        self._matrix = np.array([record.embedding for record in records], dtype=np.float32)

    async def search(
        self,
        query: str,
        *,
        mode: SearchMode,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
    ) -> list[SearchResult]:
        if top_k < 1:
            raise ValueError("top_k debe ser positivo")
        eligible = [
            index
            for index, record in enumerate(self._records)
            if scope.permits(record.metadata)
            and all(record.metadata.get(key) == value for key, value in filters.items())
        ]
        if not eligible:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        rankings: list[list[SearchResult]] = []
        if mode in ("bm25", "hybrid"):
            nonempty = [index for index in eligible if self._tokens[index]]
            if nonempty:
                scores = BM25Plus([self._tokens[index] for index in nonempty]).get_scores(tokens)
                lexical = [
                    SearchResult(self._records[index], float(score))
                    for index, score in zip(nonempty, scores, strict=True)
                    if set(tokens).intersection(self._tokens[index])
                ]
                rankings.append(
                    sorted(lexical, key=lambda result: (-result.score, result.record.id))
                )
            else:
                rankings.append([])
        if mode in ("vector", "hybrid"):
            vector = np.array(await self._embeddings.embed_query(query), dtype=np.float32)
            if vector.shape != (self._embeddings.dimensions,):
                raise ValueError("Dimensiones de consulta incompatibles")
            matrix = self._matrix[eligible]
            norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(vector)
            scores = (matrix @ vector) / np.maximum(norms, 1e-12)
            semantic = [
                SearchResult(self._records[index], float(score))
                for index, score in zip(eligible, scores, strict=True)
                if score > 1e-6
            ]
            rankings.append(sorted(semantic, key=lambda result: (-result.score, result.record.id)))
        if mode == "hybrid":
            return reciprocal_rank_fusion([ranking[:top_k] for ranking in rankings], top_k=top_k)
        return rankings[0][:top_k]

    async def get(self, record_id: str, *, scope: AccessScope) -> VectorRecord | None:
        record = self._sources.get(record_id)
        return record if record is not None and scope.permits(record.metadata) else None


class TokenCoverageReranker:
    async def rerank(
        self, query: str, candidates: Sequence[SearchResult], *, top_k: int
    ) -> list[SearchResult]:
        terms = set(tokenize(query))
        ranked = [
            SearchResult(
                result.record,
                len(terms.intersection(tokenize(result.record.text))) / max(len(terms), 1),
            )
            for result in candidates
        ]
        return sorted(ranked, key=lambda result: -result.score)[:top_k]

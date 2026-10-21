"""Caché semántica (fase 10.3): reutilizar respuestas de preguntas parecidas sin equivocarse.

    lookup: ¿personal? → saltar. Embedding → vecinos del mismo espacio → el primero que supere
            el umbral **y** tenga las mismas facetas.
    store:  solo si la pregunta no es personal y la respuesta es reutilizable (con citas).

Cada decisión deja un motivo (`hit`, `miss`, `below_threshold`, `facet_mismatch`, `personal`,
`unavailable`) para medir la tasa de aciertos y, sobre todo, por qué se falla.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from headache_core.application.telemetry import genai, get_telemetry
from headache_core.domain.semantic_cache import (
    CacheScope,
    QueryFacets,
    extract_facets,
)
from headache_core.ports.semantic_cache import (
    CacheEntry,
    SemanticCachePort,
    SemanticCacheUnavailableError,
)
from headache_core.ports.vector_store import EmbeddingPort

logger = logging.getLogger(__name__)

type LookupReason = Literal[
    "hit", "miss", "below_threshold", "facet_mismatch", "personal", "unavailable"
]


@dataclass(frozen=True, slots=True)
class CacheLookup:
    question: str
    scope: CacheScope
    facets: QueryFacets
    reason: LookupReason
    embedding: tuple[float, ...] = ()
    payload: str | None = None
    similarity: float = 0.0
    # Pregunta original de la entrada reutilizada (auditoría de falsos aciertos).
    matched_question: str | None = None

    @property
    def hit(self) -> bool:
        return self.payload is not None


@dataclass
class SemanticCache:
    store: SemanticCachePort
    embeddings: EmbeddingPort
    threshold: float = 0.9
    ttl_seconds: int = 3600
    candidates: int = 5
    # Sin la guarda de facetas solo manda la similitud. Existe para medir por qué hace falta.
    facet_guard: bool = True
    outcomes: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        if not 0 < self.threshold <= 1:
            raise ValueError("El umbral de similitud va de 0 (excluido) a 1.")

    async def lookup(self, question: str, scope: CacheScope) -> CacheLookup:
        with get_telemetry().span("semantic_cache.lookup") as span:
            lookup = await self._lookup(question, scope)
            span.set_attributes(
                {
                    genai.HEADACHE_CACHE_REASON: lookup.reason,
                    genai.HEADACHE_CACHE_SIMILARITY: round(lookup.similarity, 4),
                }
            )
            return lookup

    async def _lookup(self, question: str, scope: CacheScope) -> CacheLookup:
        facets = extract_facets(question)
        if facets.personal:
            return self._count(CacheLookup(question, scope, facets, "personal"))
        embedding = tuple(await self.embeddings.embed_query(question))
        try:
            neighbours = await self.store.nearest(scope.key(), embedding, limit=self.candidates)
        except SemanticCacheUnavailableError:
            logger.warning("Caché semántica no disponible: se salta.")
            return self._count(CacheLookup(question, scope, facets, "unavailable", embedding))
        reason: LookupReason = "miss"
        for candidate in neighbours:
            if candidate.similarity < self.threshold:
                reason = "below_threshold" if reason == "miss" else reason
                break
            stored = QueryFacets.from_dict(json.loads(candidate.entry.facets))
            if self.facet_guard and not facets.matches(stored):
                reason = "facet_mismatch"
                continue
            return self._count(
                CacheLookup(
                    question,
                    scope,
                    facets,
                    "hit",
                    embedding,
                    payload=candidate.entry.payload,
                    similarity=candidate.similarity,
                    matched_question=candidate.entry.question,
                )
            )
        best = neighbours[0].similarity if neighbours else 0.0
        return self._count(CacheLookup(question, scope, facets, reason, embedding, similarity=best))

    async def store_answer(self, lookup: CacheLookup, payload: str) -> bool:
        """Guarda la respuesta de un fallo. Devuelve si se guardó."""
        if lookup.facets.personal or lookup.hit or not lookup.embedding:
            return False
        with get_telemetry().span("semantic_cache.store"):
            return await self._store(lookup, payload)

    async def _store(self, lookup: CacheLookup, payload: str) -> bool:
        entry = CacheEntry(
            id=uuid.uuid4().hex,
            scope=lookup.scope.key(),
            question=lookup.question,
            facets=json.dumps(lookup.facets.to_dict(), sort_keys=True),
            embedding=lookup.embedding,
            payload=payload,
        )
        try:
            await self.store.put(entry, ttl_seconds=self.ttl_seconds)
        except SemanticCacheUnavailableError:
            logger.warning("Caché semántica no disponible: no se guarda.")
            return False
        self.outcomes["stored"] += 1
        return True

    def _count(self, lookup: CacheLookup) -> CacheLookup:
        self.outcomes[lookup.reason] += 1
        return lookup

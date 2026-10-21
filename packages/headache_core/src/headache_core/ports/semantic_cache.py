"""Almacén de la caché semántica (fase 10.3): búsqueda por vecinos dentro de un espacio.

El almacén solo sabe de vectores, espacios y caducidad. Qué se reutiliza lo decide la capa de
aplicación con las reglas del dominio (`domain/semantic_cache.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CacheEntry:
    id: str
    scope: str
    question: str
    # `QueryFacets.to_dict()` serializado: se compara al leer, no se indexa.
    facets: str
    embedding: tuple[float, ...]
    # Respuesta serializada (JSON): el almacén no conoce su forma.
    payload: str


@dataclass(frozen=True, slots=True)
class CacheCandidate:
    entry: CacheEntry
    similarity: float


class SemanticCacheUnavailableError(Exception):
    """El almacén no responde: la caché se salta (nunca tumba la petición)."""


class SemanticCachePort(Protocol):
    async def nearest(
        self, scope: str, embedding: Sequence[float], *, limit: int
    ) -> list[CacheCandidate]:
        """Los `limit` vecinos más parecidos del espacio, del más al menos similar."""
        ...

    async def put(self, entry: CacheEntry, *, ttl_seconds: int) -> None: ...

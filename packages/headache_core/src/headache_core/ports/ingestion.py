"""Puerto del registro de ingesta: qué versión de cada documento hay indexada y en qué chunks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    index_name: str
    document_id: str
    content_hash: str
    chunk_ids: tuple[str, ...]
    # Chunker + modelo de embeddings: si cambia, hay que reindexar aunque el texto no cambie.
    pipeline_version: str


class IngestionLedgerPort(Protocol):
    async def get(self, index_name: str, document_id: str) -> LedgerEntry | None: ...

    async def save(self, entry: LedgerEntry) -> None: ...

    async def delete(self, index_name: str, document_id: str) -> None: ...

    async def forget_index(self, index_name: str) -> int:
        """Olvida todo un destino (p. ej., recreado vacío); devuelve las entradas borradas."""
        ...

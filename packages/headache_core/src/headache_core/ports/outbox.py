"""Transactional outbox (fase 10.5): guardar el estado y "publicar" en la misma transacción.

Sin outbox, un worker guarda el caso y después publica en la cola; si cae entre medias, el
caso queda guardado y nadie se entera (o se publica y no se guarda). Con outbox, el evento se
inserta en una tabla **en la misma transacción** que el cambio de estado, y un *relay* lo lee
y lo publica después. O pasan las dos cosas o ninguna; la publicación llega *at-least-once*
(si el relay cae tras publicar y antes de marcar, se publica otra vez: los consumidores ya son
idempotentes por el `CaseStore`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """Un evento por publicar, escrito junto con el cambio de estado que lo origina."""

    topic: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PendingEvent:
    id: int
    topic: str
    payload: Mapping[str, Any]
    attempts: int


class OutboxStore(Protocol):
    async def claim(
        self, *, limit: int, lease_seconds: float, topic_prefix: str = ""
    ) -> list[PendingEvent]:
        """Reserva hasta `limit` eventos pendientes durante `lease_seconds`.

        Varios relays a la vez no se pisan (`FOR UPDATE SKIP LOCKED`); si uno cae, sus eventos
        vuelven a estar disponibles al vencer la reserva.
        """
        ...

    async def mark_published(self, ids: Sequence[int]) -> None: ...

    async def release(self, event_id: int, *, error: str, retry_in_seconds: float) -> None:
        """No se pudo publicar: vuelve a la cola dentro de `retry_in_seconds`."""
        ...

    async def backlog(self, *, topic_prefix: str = "") -> int:
        """Eventos sin publicar (métrica de salud y de autoescalado del relay)."""
        ...

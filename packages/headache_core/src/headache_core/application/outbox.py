"""Relay del outbox (fase 10.5): lee eventos pendientes y los publica en el broker.

- Reserva un lote (`claim`), publica uno a uno y marca los publicados. Si el broker falla con
  un evento, ese evento vuelve a la cola con *backoff* exponencial y el resto sigue.
- Si el relay cae a mitad, la reserva vence y otro relay (u otra réplica) lo retoma.
- Varios relays en paralelo no se pisan (`FOR UPDATE SKIP LOCKED`): se escala como cualquier
  consumidor. El atraso (`backlog`) es la métrica para alertar o autoescalar.

Equivalentes gestionados: *change data capture* (Debezium sobre el WAL de PostgreSQL), el
*change feed* de Cosmos DB o DynamoDB Streams + EventBridge Pipes. Leen el log de la base de
datos en vez de una tabla, sin *polling*.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from headache_core.application.telemetry import get_telemetry, metrics
from headache_core.ports import MessageBusPort
from headache_core.ports.outbox import OutboxStore

logger = logging.getLogger(__name__)


class OutboxRelay:
    def __init__(
        self,
        store: OutboxStore,
        bus: MessageBusPort,
        *,
        batch_size: int = 100,
        lease_seconds: float = 30.0,
        max_backoff_seconds: float = 60.0,
        topic_prefix: str = "",
    ) -> None:
        self._store = store
        self._bus = bus
        self._batch_size = batch_size
        self._lease = lease_seconds
        self._max_backoff = max_backoff_seconds
        self._prefix = topic_prefix
        self.published = 0
        self.failed = 0

    async def run_once(self) -> int:
        """Un lote. Devuelve cuántos eventos se publicaron."""
        events = await self._store.claim(
            limit=self._batch_size, lease_seconds=self._lease, topic_prefix=self._prefix
        )
        done: list[int] = []
        telemetry = get_telemetry()
        for event in events:
            try:
                # Continúa la traza de quien escribió el evento (guardada en el outbox), no la
                # del bucle del relay: si no, cada caso se partiría en dos trazas.
                with telemetry.span(
                    "outbox.relay",
                    parent=event.headers,
                    attributes={
                        "headache.outbox.event_id": event.id,
                        "headache.outbox.attempts": event.attempts,
                    },
                ):
                    await self._bus.publish(event.topic, event.payload, headers=event.headers)
            except Exception as exc:
                self.failed += 1
                retry = min(self._max_backoff, 2.0 ** (event.attempts - 1))
                logger.warning(
                    "Outbox %d no publicado (%s): reintento en %.0f s", event.id, exc, retry
                )
                await self._store.release(event.id, error=str(exc), retry_in_seconds=retry)
                continue
            done.append(event.id)
        if done:
            # Si se cae justo aquí, estos eventos se publicarán otra vez: at-least-once.
            await self._store.mark_published(done)
            self.published += len(done)
        return len(done)

    async def drain(self, *, max_rounds: int = 100) -> int:
        """Publica hasta vaciar lo disponible (tests y arranque)."""
        total = 0
        for _ in range(max_rounds):
            published = await self.run_once()
            total += published
            if published == 0:
                break
        return total

    async def report_backlog(self) -> int:
        """Mide el atraso y lo publica como métrica (la base de la alerta de cola atascada)."""
        backlog = await self._store.backlog(topic_prefix=self._prefix)
        metrics.outbox_backlog(backlog, topic_prefix=self._prefix)
        return backlog

    async def run(
        self,
        stop: asyncio.Event,
        *,
        idle_seconds: float = 0.5,
        backlog_every_seconds: float = 15.0,
    ) -> None:
        """Bucle del relay: sin trabajo, espera `idle_seconds` (o hasta que se pida parar).

        El atraso se mide cada `backlog_every_seconds`, no en cada vuelta: es un `COUNT` sobre
        la tabla y el relay da varias vueltas por segundo.
        """
        loop = asyncio.get_running_loop()
        next_report = loop.time()
        while not stop.is_set():
            try:
                published = await self.run_once()
                if loop.time() >= next_report:
                    await self.report_backlog()
                    next_report = loop.time() + backlog_every_seconds
            except Exception:
                logger.exception("Fallo del relay del outbox; se reintenta")
                published = 0
            if published == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), idle_seconds)

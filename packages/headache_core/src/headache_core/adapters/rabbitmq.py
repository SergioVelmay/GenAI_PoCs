"""Adaptador RabbitMQ (aio-pika) del puerto de mensajería. Requiere `headache-core[rabbitmq]`.

Topología por topic (todo idempotente: productor y workers la declaran al arrancar):

    exchange `headache.events` (topic) --routing key = topic--> cola `<topic>` (quorum)
        tras `max_deliveries` entregas fallidas o un rechazo  --> exchange `headache.events.dlx`
    exchange `headache.events.dlx` --routing key = topic--> cola `<topic>.dlq` (quorum)

Equivalencias: SQS `maxReceiveCount` + redrive policy; Service Bus `MaxDeliveryCount` + DLQ.

Requiere RabbitMQ >= 4.3: desde esa versión solo `basic.reject` (no `basic.nack`) incrementa
`x-delivery-count`, y las quorum queues ofrecen *delayed retry* (backoff hecho por el broker).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Self

import aio_pika
from aio_pika.abc import (
    AbstractChannel,
    AbstractExchange,
    AbstractIncomingMessage,
    AbstractQueue,
    AbstractRobustConnection,
)

from headache_core.config import RabbitMQSettings
from headache_core.ports import Message, MessageHandler, PermanentMessageError
from headache_core.ports.messaging import propagated_headers

logger = logging.getLogger(__name__)


class RabbitMQMessageBus:
    def __init__(
        self,
        connection: AbstractRobustConnection,
        channel: AbstractChannel,
        exchange: AbstractExchange,
        dead_letter_exchange: AbstractExchange,
        *,
        max_deliveries: int,
        retry_delay_ms: int,
    ) -> None:
        self._connection = connection
        self._channel = channel
        self._exchange = exchange
        self._dlx = dead_letter_exchange
        self._max_deliveries = max_deliveries
        self._retry_delay_ms = retry_delay_ms
        self._queues: dict[str, AbstractQueue] = {}

    @classmethod
    async def connect(
        cls,
        settings: RabbitMQSettings,
        *,
        exchange: str = "headache.events",
        prefetch: int = 8,
        max_deliveries: int = 5,
        retry_delay_ms: int = 1_000,
    ) -> Self:
        connection = await aio_pika.connect_robust(settings.url())
        channel = await connection.channel(publisher_confirms=True)
        # prefetch = mensajes en vuelo por worker = concurrencia dentro del proceso.
        await channel.set_qos(prefetch_count=prefetch)
        main = await channel.declare_exchange(exchange, aio_pika.ExchangeType.TOPIC, durable=True)
        dlx = await channel.declare_exchange(
            f"{exchange}.dlx", aio_pika.ExchangeType.TOPIC, durable=True
        )
        return cls(
            connection,
            channel,
            main,
            dlx,
            max_deliveries=max_deliveries,
            retry_delay_ms=retry_delay_ms,
        )

    async def close(self) -> None:
        await self._connection.close()

    async def publish(
        self,
        topic: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Message:
        await self._ensure_queue(topic)
        message = Message(topic=topic, payload=payload, headers=propagated_headers(headers))
        await self._exchange.publish(
            aio_pika.Message(
                json.dumps(dict(payload), ensure_ascii=False).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=message.id,
                timestamp=message.published_at,
                type=topic,
                # Contexto de traza en las cabeceras AMQP (no en el cuerpo).
                headers=dict(message.headers),
            ),
            routing_key=topic,
            # Sin cola enlazada el broker devuelve el mensaje y aio-pika lanza un error.
            mandatory=True,
        )
        return message

    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        queue = await self._ensure_queue(topic)

        async def on_message(incoming: AbstractIncomingMessage) -> None:
            await self._handle(topic, incoming, handler)

        await queue.consume(on_message)

    async def _handle(
        self, topic: str, incoming: AbstractIncomingMessage, handler: MessageHandler
    ) -> None:
        # Las quorum queues cuentan las entregas fallidas en la cabecera x-delivery-count.
        failed = incoming.headers.get("x-delivery-count")
        attempt = (failed if isinstance(failed, int) else 0) + 1
        try:
            payload = json.loads(incoming.body)
        except ValueError as exc:  # Incluye JSONDecodeError y UnicodeDecodeError.
            await self._dead_letter(incoming, exc)
            return
        if not isinstance(payload, dict):
            await self._dead_letter(incoming, "el cuerpo debe ser un objeto JSON")
            return
        try:
            await handler(
                Message(
                    topic=topic,
                    payload=payload,
                    id=incoming.message_id or "",
                    published_at=incoming.timestamp or datetime.now(UTC),
                    delivery_count=attempt,
                    headers=propagated_headers(incoming.headers),
                )
            )
        except PermanentMessageError as exc:
            await self._dead_letter(incoming, exc)
        except Exception:
            if attempt >= self._max_deliveries:
                logger.exception(
                    "Mensaje %s agotó %d entregas: a la DLQ", incoming.message_id, attempt
                )
            else:
                logger.exception(
                    "Fallo transitorio en %s (entrega %d)", incoming.message_id, attempt
                )
            # reject (no nack): cuenta como entrega fallida para x-delivery-limit y el retardo.
            await incoming.reject(requeue=True)
        else:
            await incoming.ack()

    async def _dead_letter(self, incoming: AbstractIncomingMessage, reason: object) -> None:
        logger.error("Mensaje %s irrecuperable, a la DLQ: %s", incoming.message_id, reason)
        await incoming.reject(requeue=False)

    async def _ensure_queue(self, topic: str) -> AbstractQueue:
        if topic in self._queues:
            return self._queues[topic]
        dlq = await self._channel.declare_queue(
            f"{topic}.dlq", durable=True, arguments={"x-queue-type": "quorum"}
        )
        await dlq.bind(self._dlx, routing_key=topic)
        queue = await self._channel.declare_queue(
            topic,
            durable=True,
            arguments={
                "x-queue-type": "quorum",
                # Reentregas permitidas tras la primera: N entregas en total.
                "x-delivery-limit": self._max_deliveries - 1,
                "x-dead-letter-exchange": self._dlx.name,
                "x-dead-letter-routing-key": topic,
                # El mensaje no se pierde si la DLQ no confirma (requiere reject-publish).
                "x-dead-letter-strategy": "at-least-once",
                "x-overflow": "reject-publish",
                # Backoff lineal en el broker: retardo = min(min * entregas fallidas, max).
                # El worker no bloquea un hueco del prefetch esperando.
                "x-delayed-retry-type": "failed",
                "x-delayed-retry-min": self._retry_delay_ms,
                "x-delayed-retry-max": self._retry_delay_ms * 10,
            },
        )
        await queue.bind(self._exchange, routing_key=topic)
        self._queues[topic] = queue
        return queue

"""Adaptador Azure Service Bus (`azure-servicebus`, AMQP) del puerto de mensajería.

Diferencias de semántica frente a RabbitMQ:
- Las colas (con `MaxDeliveryCount` y su DLQ integrada `<cola>/$deadletterqueue`) las crea la
  infraestructura (Bicep/Terraform en Azure, config.json en el emulador).
- Peek-lock: el mensaje queda bloqueado mientras se procesa. `complete` lo borra,
  `abandon` lo devuelve (cuenta como entrega) y `dead_letter` lo manda a la DLQ con motivo.
- `abandon` reintenta al momento, sin retardo: para backoff hay que programar una copia
  (`scheduled_enqueue_time`) o diferir el mensaje (`defer`).
- En la nube se autentica con Entra ID (Managed Identity), sin connection strings.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Self

from azure.servicebus import ServiceBusMessage, ServiceBusReceivedMessage
from azure.servicebus.aio import ServiceBusClient, ServiceBusReceiver, ServiceBusSender

from headache_core.config import ServiceBusSettings
from headache_core.ports import Message, MessageHandler, PermanentMessageError

logger = logging.getLogger(__name__)


class ServiceBusMessageBus:
    def __init__(
        self,
        client: ServiceBusClient,
        *,
        prefetch: int = 8,
        max_wait_s: int = 5,
        owned_credential: Any = None,
    ) -> None:
        self._client = client
        # La credencial asíncrona abre su propia sesión HTTP: quien la crea, la cierra.
        self._owned_credential = owned_credential
        self._prefetch = prefetch
        self._max_wait_s = max_wait_s
        self._senders: dict[str, ServiceBusSender] = {}
        self._receivers: list[ServiceBusReceiver] = []
        self._tasks: set[asyncio.Task[None]] = set()

    @classmethod
    def from_settings(cls, settings: ServiceBusSettings, *, prefetch: int = 8) -> Self:
        if settings.connection_string is not None:
            client = ServiceBusClient.from_connection_string(
                settings.connection_string.get_secret_value()
            )
            return cls(client, prefetch=prefetch, max_wait_s=settings.max_wait_s)
        if not settings.fully_qualified_namespace:
            raise ValueError(
                "Falta SERVICE_BUS__FULLY_QUALIFIED_NAMESPACE (o la connection string local)."
            )
        from azure.identity.aio import DefaultAzureCredential

        credential = DefaultAzureCredential()
        return cls(
            ServiceBusClient(settings.fully_qualified_namespace, credential),
            prefetch=prefetch,
            max_wait_s=settings.max_wait_s,
            owned_credential=credential,
        )

    async def publish(self, topic: str, payload: Mapping[str, Any]) -> Message:
        message = Message(topic=topic, payload=payload)
        if topic not in self._senders:
            # Un sender es un enlace AMQP: se reutiliza en vez de abrir uno por mensaje.
            self._senders[topic] = self._client.get_queue_sender(topic)
        await self._senders[topic].send_messages(
            ServiceBusMessage(
                json.dumps(dict(payload), ensure_ascii=False),
                message_id=message.id,
                content_type="application/json",
                subject=topic,
                application_properties={"published_at": message.published_at.isoformat()},
            )
        )
        return message

    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        receiver = self._client.get_queue_receiver(topic, prefetch_count=self._prefetch)
        self._receivers.append(receiver)
        task = asyncio.create_task(self._consume(topic, receiver, handler))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for handle in [*self._receivers, *self._senders.values()]:
            await handle.close()
        await self._client.close()
        if self._owned_credential is not None:
            await self._owned_credential.close()

    async def _consume(
        self, topic: str, receiver: ServiceBusReceiver, handler: MessageHandler
    ) -> None:
        while True:
            batch = await receiver.receive_messages(
                max_message_count=self._prefetch, max_wait_time=self._max_wait_s
            )
            await asyncio.gather(*(self._handle(topic, receiver, m, handler) for m in batch))

    async def _handle(
        self,
        topic: str,
        receiver: ServiceBusReceiver,
        raw: ServiceBusReceivedMessage,
        handler: MessageHandler,
    ) -> None:
        # delivery_count cuenta las entregas previas: vale 0 en la primera.
        attempt = (raw.delivery_count or 0) + 1
        message_id = str(raw.message_id or "")
        try:
            payload = json.loads(b"".join(raw.body))
        except ValueError as exc:
            await self._dead_letter(receiver, raw, exc)
            return
        if not isinstance(payload, dict):
            await self._dead_letter(receiver, raw, "no es un objeto JSON")
            return
        properties = raw.application_properties or {}
        published = properties.get(b"published_at") or properties.get("published_at")
        if isinstance(published, bytes):
            published = published.decode()
        try:
            await handler(
                Message(
                    topic=topic,
                    payload=payload,
                    id=message_id,
                    published_at=datetime.fromisoformat(published)
                    if isinstance(published, str)
                    else datetime.now(UTC),
                    delivery_count=attempt,
                )
            )
        except PermanentMessageError as exc:
            await self._dead_letter(receiver, raw, exc)
        except Exception:
            logger.exception("Fallo transitorio en %s (entrega %d)", message_id, attempt)
            # Al llegar a MaxDeliveryCount, Service Bus lo mueve solo a la DLQ.
            await receiver.abandon_message(raw)
        else:
            await receiver.complete_message(raw)

    async def _dead_letter(
        self, receiver: ServiceBusReceiver, raw: ServiceBusReceivedMessage, reason: object
    ) -> None:
        logger.error("Mensaje %s irrecuperable, a la DLQ: %s", raw.message_id, reason)
        await receiver.dead_letter_message(
            raw, reason="PermanentMessageError", error_description=str(reason)[:1024]
        )

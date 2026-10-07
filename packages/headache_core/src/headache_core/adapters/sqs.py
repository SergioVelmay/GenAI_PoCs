"""Adaptador Amazon SQS (boto3) del puerto de mensajería. En local, contra ElasticMQ.

Diferencias de semántica frente a RabbitMQ (y cómo se resuelven):
- La cola y su DLQ (redrive policy con `maxReceiveCount`) las crea la infraestructura
  (Terraform en AWS, elasticmq.conf en local). La app solo envía, recibe y borra.
- SQS no tiene "rechazar a la DLQ": un error permanente se copia a mano a la DLQ y se borra.
- El backoff es nativo: al fallar se amplía la visibility timeout del mensaje.
- Los nombres no admiten puntos: el topic `knowledge.documents` es la cola `knowledge-documents`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self

import boto3

from headache_core.config import SqsSettings
from headache_core.ports import Message, MessageHandler, PermanentMessageError

if TYPE_CHECKING:
    from mypy_boto3_sqs import SQSClient
    from mypy_boto3_sqs.type_defs import MessageTypeDef

logger = logging.getLogger(__name__)

# Máximo de SQS para la visibility timeout (12 h).
MAX_VISIBILITY_S = 43_200


def queue_name(topic: str) -> str:
    return topic.replace(".", "-")


def _attribute(raw: MessageTypeDef, name: str) -> str | None:
    value = raw.get("MessageAttributes", {}).get(name)
    return value.get("StringValue") if value else None


class SqsMessageBus:
    def __init__(
        self,
        client: SQSClient,
        *,
        wait_time_s: int = 10,
        batch_size: int = 10,
        retry_delay_s: int = 1,
    ) -> None:
        self._client = client
        self._wait_time_s = wait_time_s
        self._batch_size = min(batch_size, 10)  # Límite de ReceiveMessage.
        self._retry_delay_s = retry_delay_s
        self._urls: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @classmethod
    def from_settings(
        cls, settings: SqsSettings, *, batch_size: int = 10, retry_delay_s: int = 1
    ) -> Self:
        session = boto3.Session(profile_name=settings.profile or None, region_name=settings.region)
        if settings.endpoint_url:
            # ElasticMQ no valida la firma, pero boto3 exige credenciales: valen unas ficticias.
            client = session.client(
                "sqs",
                endpoint_url=settings.endpoint_url,
                aws_access_key_id="local",
                aws_secret_access_key="local",
            )
        else:
            client = session.client("sqs")
        return cls(
            client,
            wait_time_s=settings.wait_time_s,
            batch_size=batch_size,
            retry_delay_s=retry_delay_s,
        )

    async def publish(self, topic: str, payload: Mapping[str, Any]) -> Message:
        message = Message(topic=topic, payload=payload)
        url = await self._queue_url(queue_name(topic))
        # boto3 es síncrono: cada llamada va a un hilo para no bloquear el event loop.
        await asyncio.to_thread(
            self._client.send_message,
            QueueUrl=url,
            MessageBody=json.dumps(dict(payload), ensure_ascii=False),
            MessageAttributes={
                "message_id": {"DataType": "String", "StringValue": message.id},
                "published_at": {
                    "DataType": "String",
                    "StringValue": message.published_at.isoformat(),
                },
            },
        )
        return message

    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        url = await self._queue_url(queue_name(topic))
        dlq_url = await self._dead_letter_url(url)
        task = asyncio.create_task(self._poll(topic, url, dlq_url, handler))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _poll(self, topic: str, url: str, dlq_url: str, handler: MessageHandler) -> None:
        while True:
            response = await asyncio.to_thread(
                self._client.receive_message,
                QueueUrl=url,
                MaxNumberOfMessages=self._batch_size,
                WaitTimeSeconds=self._wait_time_s,
                MessageSystemAttributeNames=["ApproximateReceiveCount"],
                MessageAttributeNames=["All"],
            )
            batch = response.get("Messages", [])
            await asyncio.gather(*(self._handle(topic, url, dlq_url, m, handler) for m in batch))

    async def _handle(
        self,
        topic: str,
        url: str,
        dlq_url: str,
        raw: MessageTypeDef,
        handler: MessageHandler,
    ) -> None:
        attempt = int(raw.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        message_id = _attribute(raw, "message_id") or raw.get("MessageId", "")
        published = _attribute(raw, "published_at")
        try:
            payload = json.loads(raw.get("Body", ""))
        except ValueError as exc:
            await self._dead_letter(url, dlq_url, raw, message_id, exc)
            return
        if not isinstance(payload, dict):
            await self._dead_letter(url, dlq_url, raw, message_id, "no es un objeto JSON")
            return
        try:
            await handler(
                Message(
                    topic=topic,
                    payload=payload,
                    id=message_id,
                    published_at=datetime.fromisoformat(published)
                    if published
                    else datetime.now(UTC),
                    delivery_count=attempt,
                )
            )
        except PermanentMessageError as exc:
            await self._dead_letter(url, dlq_url, raw, message_id, exc)
        except Exception:
            logger.exception("Fallo transitorio en %s (entrega %d)", message_id, attempt)
            # Backoff lineal: el mensaje no reaparece hasta pasado este tiempo. Al superar
            # maxReceiveCount, SQS lo mueve solo a la DLQ en la siguiente recepción.
            await asyncio.to_thread(
                self._client.change_message_visibility,
                QueueUrl=url,
                ReceiptHandle=raw["ReceiptHandle"],
                VisibilityTimeout=min(self._retry_delay_s * attempt, MAX_VISIBILITY_S),
            )
        else:
            await asyncio.to_thread(
                self._client.delete_message, QueueUrl=url, ReceiptHandle=raw["ReceiptHandle"]
            )

    async def _dead_letter(
        self, url: str, dlq_url: str, raw: MessageTypeDef, message_id: str, reason: object
    ) -> None:
        logger.error("Mensaje %s irrecuperable, a la DLQ: %s", message_id, reason)
        await asyncio.to_thread(
            self._client.send_message,
            QueueUrl=dlq_url,
            MessageBody=raw.get("Body", ""),
            MessageAttributes={
                "message_id": {"DataType": "String", "StringValue": message_id},
                "dead_letter_reason": {"DataType": "String", "StringValue": str(reason)[:1024]},
            },
        )
        await asyncio.to_thread(
            self._client.delete_message, QueueUrl=url, ReceiptHandle=raw["ReceiptHandle"]
        )

    async def _queue_url(self, name: str) -> str:
        if name not in self._urls:
            response = await asyncio.to_thread(self._client.get_queue_url, QueueName=name)
            self._urls[name] = response["QueueUrl"]
        return self._urls[name]

    async def _dead_letter_url(self, url: str) -> str:
        response = await asyncio.to_thread(
            self._client.get_queue_attributes, QueueUrl=url, AttributeNames=["RedrivePolicy"]
        )
        policy = response.get("Attributes", {}).get("RedrivePolicy")
        if not policy:
            raise ValueError(f"La cola {url} no tiene DLQ (redrive policy) configurada.")
        dlq_arn: str = json.loads(policy)["deadLetterTargetArn"]
        return await self._queue_url(dlq_arn.rsplit(":", 1)[1])

"""Profundidad de las colas para el autoescalado (fase 10.6).

- RabbitMQ: API HTTP de gestión (`/api/queues/<vhost>/<cola>`), la misma que usa el escalador
  `rabbitmq` de KEDA en modo HTTP: `messages_ready` + `messages_unacknowledged`.
- SQS: `ApproximateNumberOfMessages` + `ApproximateNumberOfMessagesNotVisible` (aproximadas,
  con retardo de hasta ~1 min en CloudWatch; la API directa responde al momento).
- Service Bus (no implementado aquí): `active_message_count` del cliente de administración, lo
  que lee el escalador `azure-servicebus` de KEDA.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self
from urllib.parse import quote

import httpx

from headache_core.config import RabbitMQSettings, SqsSettings
from headache_core.ports.scaling import QueueDepth, QueueUnavailableError

if TYPE_CHECKING:
    from mypy_boto3_sqs import SQSClient


class RabbitMQManagementMetrics:
    def __init__(self, client: httpx.AsyncClient, *, vhost: str = "/") -> None:
        self._client = client
        self._vhost = quote(vhost, safe="")

    @classmethod
    def from_settings(
        cls, settings: RabbitMQSettings, *, base_url: str = "http://127.0.0.1:15672"
    ) -> Self:
        client = httpx.AsyncClient(
            base_url=base_url,
            auth=(settings.user, settings.password.get_secret_value()),
            timeout=2.0,
        )
        return cls(client)

    async def depth(self, queue: str) -> QueueDepth:
        try:
            response = await self._client.get(f"/api/queues/{self._vhost}/{quote(queue, safe='')}")
        except httpx.HTTPError as exc:
            raise QueueUnavailableError(str(exc)) from exc
        if response.status_code == 404:
            # La cola aún no existe (nadie ha publicado ni se ha suscrito): está vacía.
            return QueueDepth(0)
        if response.status_code != 200:
            raise QueueUnavailableError(f"HTTP {response.status_code}")
        data = response.json()
        return QueueDepth(
            ready=int(data.get("messages_ready", 0)),
            in_flight=int(data.get("messages_unacknowledged", 0)),
            consumers=int(data.get("consumers", 0)),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class SqsQueueMetrics:
    def __init__(self, client: SQSClient) -> None:
        self._client = client
        self._urls: dict[str, str] = {}

    @classmethod
    def from_settings(cls, settings: SqsSettings) -> Self:
        import boto3

        session = boto3.Session(profile_name=settings.profile or None, region_name=settings.region)
        return cls(session.client("sqs", endpoint_url=settings.endpoint_url or None))

    async def depth(self, queue: str) -> QueueDepth:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return await asyncio.to_thread(self._depth, queue)
        except (BotoCoreError, ClientError) as exc:
            raise QueueUnavailableError(str(exc)) from exc

    def _depth(self, queue: str) -> QueueDepth:
        url = self._urls.get(queue)
        if url is None:
            url = self._urls[queue] = self._client.get_queue_url(QueueName=queue)["QueueUrl"]
        attributes = self._client.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
            ],
        )["Attributes"]
        return QueueDepth(
            ready=int(attributes.get("ApproximateNumberOfMessages", 0)),
            in_flight=int(attributes.get("ApproximateNumberOfMessagesNotVisible", 0)),
        )

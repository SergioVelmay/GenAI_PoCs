"""Contrato del puerto de mensajería: las mismas garantías en RabbitMQ, SQS y Service Bus.

1. Entrega y confirmación.  2. Reintento de fallos transitorios.
3. DLQ al agotar las entregas.  4. DLQ directa para errores permanentes.

Cada broker se salta si su emulador no está arrancado:
    docker compose up -d                                # RabbitMQ
    docker compose --profile sqs up -d                  # ElasticMQ
    docker compose --profile servicebus up -d           # Service Bus (requiere aceptar la EULA)
"""

from __future__ import annotations

import asyncio
import json
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import aio_pika
import boto3
import pytest
from azure.servicebus import ServiceBusSubQueue
from azure.servicebus.aio import ServiceBusClient

from headache_core.adapters.rabbitmq import RabbitMQMessageBus
from headache_core.adapters.servicebus import ServiceBusMessageBus
from headache_core.adapters.sqs import SqsMessageBus, queue_name
from headache_core.config import get_settings
from headache_core.ports import Message, MessageBusPort, PermanentMessageError

pytestmark = pytest.mark.integration

TIMEOUT_S = 30
MAX_DELIVERIES = 3
SQS_ENDPOINT = get_settings().sqs.endpoint_url or "http://127.0.0.1:9324"
SERVICE_BUS_EMULATOR = (
    "Endpoint=sb://127.0.0.1:5673;SharedAccessKeyName=RootManageSharedAccessKey;"
    "SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;"
)
# Declarada en infra/local/servicebus/config.json con MaxDeliveryCount = 3.
SERVICE_BUS_QUEUE = "it.servicebus"


@dataclass
class Broker:
    bus: MessageBusPort
    topic: str
    # Espera el primer mensaje de la DLQ y devuelve su id.
    dead_letter_id: Callable[[], Awaitable[str]]


def _require(host: str, port: int, name: str) -> None:
    try:
        socket.create_connection((host, port), timeout=0.5).close()
    except OSError:
        pytest.skip(f"{name} no está arrancado en {host}:{port}")


@pytest.fixture(params=["rabbitmq", "sqs", "servicebus"])
async def broker(request: pytest.FixtureRequest) -> AsyncIterator[Broker]:
    factories = {"rabbitmq": _rabbitmq, "sqs": _sqs, "servicebus": _servicebus}
    async for broker in factories[request.param]():
        yield broker


async def _rabbitmq() -> AsyncIterator[Broker]:
    settings = get_settings().rabbitmq
    _require(settings.host, settings.port, "RabbitMQ")
    topic = f"it.{uuid.uuid4().hex[:12]}"
    bus = await RabbitMQMessageBus.connect(
        settings, max_deliveries=MAX_DELIVERIES, retry_delay_ms=50
    )

    async def dead_letter_id() -> str:
        connection = await aio_pika.connect(settings.url())
        async with connection:
            queue = await (await connection.channel()).get_queue(f"{topic}.dlq")
            async with queue.iterator() as messages:
                async for message in messages:
                    await message.ack()
                    return message.message_id or ""
        raise AssertionError("DLQ cerrada sin mensajes")

    yield Broker(bus, topic, dead_letter_id)
    await bus.close()
    connection = await aio_pika.connect(settings.url())
    async with connection:
        channel = await connection.channel()
        for name in (topic, f"{topic}.dlq"):
            await channel.queue_delete(name)


async def _sqs() -> AsyncIterator[Broker]:
    endpoint = urlparse(SQS_ENDPOINT)
    _require(endpoint.hostname or "127.0.0.1", endpoint.port or 9324, "ElasticMQ")
    client = boto3.client(
        "sqs",
        endpoint_url=SQS_ENDPOINT,
        region_name="eu-west-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )
    topic = f"it.{uuid.uuid4().hex[:12]}"
    # El test hace de "Terraform": crea la cola con su DLQ y redrive policy.
    dlq_url = client.create_queue(QueueName=f"{queue_name(topic)}-dlq")["QueueUrl"]
    dlq_arn = client.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]
    url = client.create_queue(
        QueueName=queue_name(topic),
        Attributes={
            "VisibilityTimeout": "1",
            "RedrivePolicy": json.dumps(
                {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": str(MAX_DELIVERIES)}
            ),
        },
    )["QueueUrl"]
    bus = SqsMessageBus(client, wait_time_s=1, retry_delay_s=1)

    async def dead_letter_id() -> str:
        while True:
            response = await asyncio.to_thread(
                client.receive_message,
                QueueUrl=dlq_url,
                WaitTimeSeconds=1,
                MessageAttributeNames=["All"],
            )
            for raw in response.get("Messages", []):
                return raw.get("MessageAttributes", {})["message_id"].get("StringValue", "")

    yield Broker(bus, topic, dead_letter_id)
    await bus.close()
    for queue_url in (url, dlq_url):
        client.delete_queue(QueueUrl=queue_url)


async def _servicebus() -> AsyncIterator[Broker]:
    _require("127.0.0.1", 5673, "Emulador de Service Bus")
    connection = get_settings().service_bus.connection_string
    conn_str = connection.get_secret_value() if connection else SERVICE_BUS_EMULATOR
    admin = ServiceBusClient.from_connection_string(conn_str)
    # La cola es fija (el emulador no permite crearlas desde Python): se vacía antes.
    for sub_queue in (None, ServiceBusSubQueue.DEAD_LETTER):
        async with admin.get_queue_receiver(SERVICE_BUS_QUEUE, sub_queue=sub_queue) as drain:
            while batch := await drain.receive_messages(max_message_count=50, max_wait_time=1):
                for raw in batch:
                    await drain.complete_message(raw)
    bus = ServiceBusMessageBus(ServiceBusClient.from_connection_string(conn_str), max_wait_s=1)

    async def dead_letter_id() -> str:
        async with admin.get_queue_receiver(
            SERVICE_BUS_QUEUE, sub_queue=ServiceBusSubQueue.DEAD_LETTER
        ) as dlq:
            while True:
                for raw in await dlq.receive_messages(max_wait_time=1):
                    await dlq.complete_message(raw)
                    return str(raw.message_id)

    yield Broker(bus, SERVICE_BUS_QUEUE, dead_letter_id)
    await bus.close()
    await admin.close()


async def test_delivers_and_acks(broker: Broker) -> None:
    received: asyncio.Queue[Message] = asyncio.Queue()

    async def handler(message: Message) -> None:
        await received.put(message)

    await broker.bus.subscribe(broker.topic, handler)
    sent = await broker.bus.publish(broker.topic, {"document_id": "faq-madrid-es"})

    got = await asyncio.wait_for(received.get(), TIMEOUT_S)
    assert (got.id, dict(got.payload), got.delivery_count) == (sent.id, dict(sent.payload), 1)


async def test_transient_failure_is_retried(broker: Broker) -> None:
    attempts: list[int] = []
    done = asyncio.Event()

    async def flaky(message: Message) -> None:
        attempts.append(message.delivery_count)
        if message.delivery_count < 2:
            raise ConnectionError("pasajero")
        done.set()

    await broker.bus.subscribe(broker.topic, flaky)
    await broker.bus.publish(broker.topic, {})

    await asyncio.wait_for(done.wait(), TIMEOUT_S)
    assert attempts == [1, 2]


async def test_exhausted_retries_end_in_dlq(broker: Broker) -> None:
    attempts: list[int] = []

    async def broken(message: Message) -> None:
        attempts.append(message.delivery_count)
        raise ConnectionError("siempre falla")

    await broker.bus.subscribe(broker.topic, broken)
    sent = await broker.bus.publish(broker.topic, {"k": 1})

    assert await asyncio.wait_for(broker.dead_letter_id(), TIMEOUT_S) == sent.id
    assert attempts == list(range(1, MAX_DELIVERIES + 1))


async def test_permanent_error_goes_straight_to_dlq(broker: Broker) -> None:
    attempts: list[int] = []

    async def rejecting(message: Message) -> None:
        attempts.append(message.delivery_count)
        raise PermanentMessageError("contrato inválido")

    await broker.bus.subscribe(broker.topic, rejecting)
    sent = await broker.bus.publish(broker.topic, {})

    assert await asyncio.wait_for(broker.dead_letter_id(), TIMEOUT_S) == sent.id
    assert attempts == [1]

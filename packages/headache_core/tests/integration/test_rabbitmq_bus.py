"""RabbitMQ real: entrega, reintentos con x-delivery-count y dead-lettering a la DLQ."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import aio_pika
import pytest

from headache_core.adapters.rabbitmq import RabbitMQMessageBus
from headache_core.config import get_settings
from headache_core.ports import Message, PermanentMessageError

pytestmark = pytest.mark.integration

TIMEOUT_S = 10


@pytest.fixture
def topic() -> str:
    return f"it.{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def bus(topic: str) -> AsyncIterator[RabbitMQMessageBus]:
    bus = await RabbitMQMessageBus.connect(
        get_settings().rabbitmq, max_deliveries=3, retry_delay_ms=50
    )
    yield bus
    await bus.close()
    connection = await aio_pika.connect(get_settings().rabbitmq.url())
    async with connection:
        channel = await connection.channel()
        for name in (topic, f"{topic}.dlq"):
            await channel.queue_delete(name)


async def _dead_letter(topic: str) -> aio_pika.abc.AbstractIncomingMessage:
    connection = await aio_pika.connect(get_settings().rabbitmq.url())
    async with connection:
        channel = await connection.channel()
        queue = await channel.get_queue(f"{topic}.dlq")
        async with asyncio.timeout(TIMEOUT_S), queue.iterator() as messages:
            async for message in messages:
                await message.ack()
                return message
    raise AssertionError("La DLQ se cerró sin mensajes.")


async def test_delivers_and_acks(bus: RabbitMQMessageBus, topic: str) -> None:
    received: asyncio.Queue[Message] = asyncio.Queue()

    async def handler(message: Message) -> None:
        await received.put(message)

    await bus.subscribe(topic, handler)
    sent = await bus.publish(topic, {"document_id": "faq-madrid-es"})

    got = await asyncio.wait_for(received.get(), TIMEOUT_S)
    assert (got.id, got.payload, got.delivery_count) == (sent.id, sent.payload, 1)


async def test_transient_failure_is_retried(bus: RabbitMQMessageBus, topic: str) -> None:
    attempts: list[int] = []
    done = asyncio.Event()

    async def flaky(message: Message) -> None:
        attempts.append(message.delivery_count)
        if message.delivery_count < 2:
            raise ConnectionError("pasajero")
        done.set()

    await bus.subscribe(topic, flaky)
    await bus.publish(topic, {})

    await asyncio.wait_for(done.wait(), TIMEOUT_S)
    assert attempts == [1, 2]


async def test_exhausted_retries_end_in_dlq(bus: RabbitMQMessageBus, topic: str) -> None:
    attempts: list[int] = []

    async def broken(message: Message) -> None:
        attempts.append(message.delivery_count)
        raise ConnectionError("siempre falla")

    await bus.subscribe(topic, broken)
    sent = await bus.publish(topic, {"k": 1})

    dead = await _dead_letter(topic)
    assert dead.message_id == sent.id
    assert attempts == [1, 2, 3]


async def test_permanent_error_goes_straight_to_dlq(bus: RabbitMQMessageBus, topic: str) -> None:
    attempts: list[int] = []

    async def rejecting(message: Message) -> None:
        attempts.append(message.delivery_count)
        raise PermanentMessageError("contrato inválido")

    await bus.subscribe(topic, rejecting)
    sent = await bus.publish(topic, {})

    dead = await _dead_letter(topic)
    assert dead.message_id == sent.id
    assert attempts == [1]

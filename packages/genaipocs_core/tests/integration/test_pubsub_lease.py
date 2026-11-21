"""Latido del plazo de ack contra el emulador de Pub/Sub (10.G1, heredado de la 9.G3).

Un turno más largo que `ack_deadline_seconds` (10 s, el mínimo) con *exactly-once*:

- con latido, se entrega una vez y el `ack` se acepta;
- sin latido (control), el plazo caduca y Pub/Sub lo reentrega a **otro worker** mientras el
  primero sigue; el `ack` del primero llega tarde y falla (`ack_failures`).

Dos workers comparten la suscripción (competing consumers), como dos réplicas de un worker pool:
un worker no vuelve a pedir mensajes hasta terminar su lote, así que la reentrega la ve el otro.

    docker compose --profile pubsub up -d
    uv run pytest -m integration -k pubsub_lease
"""

from __future__ import annotations

import asyncio
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import grpc
import pytest
from google.pubsub_v1 import PublisherAsyncClient, SubscriberAsyncClient
from google.pubsub_v1.services.publisher.transports.grpc_asyncio import (
    PublisherGrpcAsyncIOTransport,
)
from google.pubsub_v1.services.subscriber.transports.grpc_asyncio import (
    SubscriberGrpcAsyncIOTransport,
)

from genaipocs_core.adapters.pubsub import (
    LOCAL_PROJECT,
    PubSubMessageBus,
    subscription_path,
    topic_path,
)
from genaipocs_core.config import GcpSettings, PubSubSettings, get_settings
from genaipocs_core.ports import Message

pytestmark = pytest.mark.integration

EMULATOR = get_settings().pubsub.emulator_host or "127.0.0.1:8085"
ACK_DEADLINE_S = 10
# Más que el plazo y que el periodo del latido (5 s), con margen para el reloj del emulador.
WORK_S = 14.0

type BusFactory = Callable[[int, str], Awaitable[PubSubMessageBus]]


@pytest.fixture
async def make_bus() -> AsyncIterator[BusFactory]:
    host, _, port = EMULATOR.rpartition(":")
    try:
        socket.create_connection((host, int(port)), timeout=0.5).close()
    except OSError:
        pytest.skip(f"El emulador de Pub/Sub no está arrancado en {EMULATOR}")
    created: list[tuple[PubSubMessageBus, str]] = []

    async def factory(max_lease_s: int, topic: str) -> PubSubMessageBus:
        settings = PubSubSettings(
            emulator_host=EMULATOR,
            ack_deadline_s=ACK_DEADLINE_S,
            exactly_once=True,
            max_lease_s=max_lease_s,
        )
        bus = PubSubMessageBus.from_settings(settings, GcpSettings(), project=LOCAL_PROJECT)
        created.append((bus, topic))
        return bus

    yield factory
    channel = grpc.aio.insecure_channel(EMULATOR)
    publisher = PublisherAsyncClient(transport=PublisherGrpcAsyncIOTransport(channel=channel))
    subscriber = SubscriberAsyncClient(transport=SubscriberGrpcAsyncIOTransport(channel=channel))
    for bus, _ in created:
        await bus.close()
    for topic in {topic for _, topic in created}:
        for suffix in ("worker", "dlq"):
            await subscriber.delete_subscription(
                subscription=subscription_path(LOCAL_PROJECT, topic, suffix)
            )
        for name in (topic, f"{topic}.dlq"):
            await publisher.delete_topic(topic=topic_path(LOCAL_PROJECT, name))
    await channel.close()


async def _long_turn(make_bus: BusFactory, max_lease_s: int) -> tuple[PubSubMessageBus, list[int]]:
    """Dos workers; el primer turno dura `WORK_S`. Devuelve el bus del primero y las entregas."""
    topic = f"it.lease.{uuid.uuid4().hex[:10]}"
    slow_bus = await make_bus(max_lease_s, topic)
    other_bus = await make_bus(max_lease_s, topic)
    deliveries: list[int] = []
    first_started = asyncio.Event()
    first_done = asyncio.Event()

    async def slow(message: Message) -> None:
        deliveries.append(message.delivery_count)
        first_started.set()
        await asyncio.sleep(WORK_S)
        first_done.set()

    async def other(message: Message) -> None:
        deliveries.append(message.delivery_count)

    await slow_bus.subscribe(topic, slow)
    await slow_bus.publish(topic, {"case_id": "case-lease"})
    await asyncio.wait_for(first_started.wait(), 10)
    # El segundo worker se suscribe cuando el mensaje ya está en manos del primero.
    await other_bus.subscribe(topic, other)
    await asyncio.wait_for(first_done.wait(), WORK_S + 10)
    # Margen para que el ack (o su fallo) se resuelva y una reentrega tardía aparezca.
    await asyncio.sleep(3)
    return slow_bus, deliveries


async def test_lease_keeps_a_long_turn_with_a_single_delivery(make_bus: BusFactory) -> None:
    bus, deliveries = await _long_turn(make_bus, 3600)

    assert deliveries == [1]
    assert bus.ack_failures == 0
    assert bus.lease_extensions >= 2


async def test_without_lease_the_long_turn_is_redelivered(make_bus: BusFactory) -> None:
    """Control: el mismo turno sin latido se reentrega y su ack caducado falla."""
    bus, deliveries = await _long_turn(make_bus, 0)

    assert len(deliveries) >= 2
    assert bus.ack_failures >= 1
    assert bus.lease_extensions == 0

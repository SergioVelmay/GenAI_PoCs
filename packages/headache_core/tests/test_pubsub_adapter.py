"""Adaptador de Pub/Sub (fase 3.G1) sin red: lo que el contrato con el emulador no cubre.

El contrato completo (entrega, reintento, DLQ) está en
`integration/test_message_bus_contract.py` contra el emulador.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from google.pubsub_v1 import PublisherAsyncClient, SubscriberAsyncClient
from google.pubsub_v1.types import (
    DeadLetterPolicy,
    PubsubMessage,
    ReceivedMessage,
    Subscription,
)

from headache_core.adapters.messaging_factory import MESSAGING_SYSTEMS
from headache_core.adapters.pubsub import PubSubMessageBus, subscription_path, topic_path
from headache_core.config import GcpSettings, PubSubSettings
from headache_core.ports import Message

PROJECT = "headache-test"
DLQ = topic_path(PROJECT, "knowledge.documents.dlq")


class FakePublisher:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.published: list[tuple[str, PubsubMessage]] = []

    async def publish(self, *, topic: str, messages: list[PubsubMessage]) -> None:
        self.calls.append(f"publish {topic}")
        self.published.extend((topic, m) for m in messages)


class FakeSubscriber:
    def __init__(self, calls: list[str], subscription: Subscription) -> None:
        self.calls = calls
        self.subscription = subscription

    async def get_subscription(self, *, subscription: str) -> Subscription:
        return self.subscription

    async def acknowledge(self, *, subscription: str, ack_ids: list[str]) -> None:
        self.calls.append(f"ack {ack_ids[0]}")

    async def modify_ack_deadline(
        self, *, subscription: str, ack_ids: list[str], ack_deadline_seconds: int
    ) -> None:
        self.calls.append(f"deadline {ack_ids[0]}={ack_deadline_seconds}")


def make_bus(subscription: Subscription | None = None) -> tuple[PubSubMessageBus, list[str]]:
    calls: list[str] = []
    publisher = FakePublisher(calls)
    subscriber = FakeSubscriber(calls, subscription or Subscription())
    bus = PubSubMessageBus(
        cast(PublisherAsyncClient, publisher),
        cast(SubscriberAsyncClient, subscriber),
        project=PROJECT,
    )
    return bus, calls


def received(data: bytes, *, attempt: int = 1) -> ReceivedMessage:
    return ReceivedMessage(
        ack_id="a1",
        delivery_attempt=attempt,
        message=PubsubMessage(data=data, attributes={"message_id": "m1"}, message_id="p1"),
    )


def test_names_keep_the_logical_topic() -> None:
    """Pub/Sub admite puntos: el tema se llama como en RabbitMQ y Service Bus."""
    assert topic_path("p", "knowledge.documents") == "projects/p/topics/knowledge.documents"
    assert subscription_path("p", "knowledge.documents", "worker") == (
        "projects/p/subscriptions/knowledge.documents.worker"
    )
    assert MESSAGING_SYSTEMS["pubsub"] == "gcp_pubsub"


def test_cloud_without_project_fails_loudly() -> None:
    with pytest.raises(ValueError, match="GCP__PROJECT"):
        PubSubMessageBus.from_settings(PubSubSettings(), GcpSettings(), project="")


async def test_refuses_a_subscription_without_dead_letter_policy() -> None:
    bus, _ = make_bus(Subscription())

    async def handler(message: Message) -> None: ...

    with pytest.raises(ValueError, match="dead-letter policy"):
        await bus.subscribe("knowledge.documents", handler)


async def test_poison_message_is_published_to_the_dlq_before_acking() -> None:
    bus, calls = make_bus()

    async def handler(message: Message) -> None:
        raise AssertionError("un mensaje que no es JSON no llega al handler")

    await bus._handle("t", "s", DLQ, received(b"no es json"), handler)

    assert calls == [f"publish {DLQ}", "ack a1"]
    publisher = cast(Any, bus._publisher)
    _, dead = publisher.published[0]
    assert dead.attributes["message_id"] == "m1"
    assert dead.attributes["dead_letter_reason"]


async def test_transient_failure_nacks_and_reports_the_attempt() -> None:
    bus, calls = make_bus(Subscription(dead_letter_policy=DeadLetterPolicy(dead_letter_topic=DLQ)))
    attempts: list[int] = []

    async def flaky(message: Message) -> None:
        attempts.append(message.delivery_count)
        raise ConnectionError("pasajero")

    await bus._handle("t", "s", DLQ, received(b"{}", attempt=3), flaky)

    assert attempts == [3]
    assert calls == ["deadline a1=0"]

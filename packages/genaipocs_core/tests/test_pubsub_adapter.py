"""Adaptador de Pub/Sub (fase 3.G1) sin red: lo que el contrato con el emulador no cubre.

El contrato completo (entrega, reintento, DLQ) está en
`integration/test_message_bus_contract.py` contra el emulador.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast

import pytest
from google.api_core import exceptions
from google.pubsub_v1 import PublisherAsyncClient, SubscriberAsyncClient
from google.pubsub_v1.types import (
    DeadLetterPolicy,
    PubsubMessage,
    ReceivedMessage,
    Subscription,
)
from google.rpc.error_details_pb2 import ErrorInfo  # type: ignore[import-untyped]

from genaipocs_core.adapters.messaging_factory import MESSAGING_SYSTEMS
from genaipocs_core.adapters.pubsub import PubSubMessageBus, subscription_path, topic_path
from genaipocs_core.config import GcpSettings, PubSubSettings
from genaipocs_core.ports import Message

PROJECT = "genaipocs-test"
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


def _ack_failure(kind: str) -> exceptions.InvalidArgument:
    # Forma del error real (comprobada con el emulador): motivo y un valor por `ack_id`.
    info = ErrorInfo(
        reason="EXACTLY_ONCE_ACKID_FAILURE",
        domain="pubsub.googleapis.com",
        metadata={"projects/p/subscriptions/s:1": kind},
    )
    return exceptions.InvalidArgument("ack fallido", error_info=info)  # type: ignore[no-untyped-call]


class FailingSubscriber(FakeSubscriber):
    def __init__(self, calls: list[str], errors: list[Exception]) -> None:
        super().__init__(calls, Subscription())
        self.errors = errors

    async def acknowledge(self, *, subscription: str, ack_ids: list[str]) -> None:
        self.calls.append(f"ack {ack_ids[0]}")
        if self.errors:
            raise self.errors.pop(0)


def make_failing_bus(errors: list[Exception]) -> tuple[PubSubMessageBus, list[str]]:
    calls: list[str] = []
    bus = PubSubMessageBus(
        cast(PublisherAsyncClient, FakePublisher(calls)),
        cast(SubscriberAsyncClient, FailingSubscriber(calls, errors)),
        project=PROJECT,
    )
    return bus, calls


async def _ok(message: Message) -> None: ...


async def test_expired_ack_with_exactly_once_is_counted_and_does_not_break_the_loop() -> None:
    """Exactly-once (9.G3): un ack caducado falla; el mensaje puede volver, el bucle sigue."""
    bus, calls = make_failing_bus([_ack_failure("PERMANENT_FAILURE_INVALID_ACK_ID")])

    await bus._handle("t", "s", DLQ, received(b"{}"), _ok)

    assert calls == ["ack a1"]  # Un fallo permanente no se reintenta.
    assert bus.ack_failures == 1


async def test_transient_ack_failure_is_retried() -> None:
    bus, calls = make_failing_bus([_ack_failure("TRANSIENT_FAILURE_UNORDERED_ACK_ID")])

    await bus._handle("t", "s", DLQ, received(b"{}"), _ok)

    assert calls == ["ack a1", "ack a1"]
    assert bus.ack_failures == 0


async def test_ack_lost_by_the_network_is_counted_too() -> None:
    lost = exceptions.ServiceUnavailable("red")  # type: ignore[no-untyped-call]
    bus, calls = make_failing_bus([lost])

    await bus._handle("t", "s", DLQ, received(b"{}"), _ok)

    assert calls == ["ack a1"]
    assert bus.ack_failures == 1


async def test_emulator_topology_carries_exactly_once() -> None:
    # Los canales grpc.aio necesitan un bucle en marcha: test asíncrono, sin conectar.
    settings = PubSubSettings(emulator_host="127.0.0.1:8085", exactly_once=True)
    bus = PubSubMessageBus.from_settings(settings, GcpSettings(), project="")

    assert bus._topology is not None and bus._topology.exactly_once is True
    await bus.close()


# --- Latido del plazo de ack (10.G1) ---------------------------------------------------------


async def _tick(_: float) -> None:
    # Sin esperas reales: cada "periodo" del latido es una vuelta del bucle de eventos.
    await asyncio.sleep(0)


async def _spin(rounds: int = 50) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


async def _until(condition: Callable[[], bool]) -> None:
    for _ in range(1000):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("la condición no se cumplió")


class LeaseSubscriber(FakeSubscriber):
    def __init__(self, calls: list[str], errors: list[Exception]) -> None:
        super().__init__(calls, Subscription())
        self.errors = errors

    async def modify_ack_deadline(
        self, *, subscription: str, ack_ids: list[str], ack_deadline_seconds: int
    ) -> None:
        await super().modify_ack_deadline(
            subscription=subscription, ack_ids=ack_ids, ack_deadline_seconds=ack_deadline_seconds
        )
        if self.errors:
            raise self.errors.pop(0)


def make_lease_bus(
    errors: list[Exception] | None = None, **kwargs: Any
) -> tuple[PubSubMessageBus, list[str]]:
    calls: list[str] = []
    bus = PubSubMessageBus(
        cast(PublisherAsyncClient, FakePublisher(calls)),
        cast(SubscriberAsyncClient, LeaseSubscriber(calls, errors or [])),
        project=PROJECT,
        sleep=_tick,
        **kwargs,
    )
    return bus, calls


async def test_lease_is_renewed_while_the_handler_works_and_stops_before_the_ack() -> None:
    """Un turno más largo que el plazo no se reentrega a otro worker (heredado de la 9.G3)."""
    bus, calls = make_lease_bus()

    async def slow(message: Message) -> None:
        await _until(lambda: calls.count("deadline a1=60") >= 2)

    await bus._handle("t", "s", DLQ, received(b"{}"), slow, ack_deadline_s=60)

    # Se renueva con el plazo de la suscripción y el ack llega el último, ya sin latido.
    assert calls[-1] == "ack a1"
    assert set(calls[:-1]) == {"deadline a1=60"}
    assert bus.lease_extensions == len(calls) - 1 >= 2
    await _spin()
    assert calls[-1] == "ack a1"  # El latido no sigue tras confirmar.


async def test_failed_handler_stops_the_lease_and_nacks() -> None:
    bus, calls = make_lease_bus()

    async def broken(message: Message) -> None:
        await _until(lambda: bool(calls))
        raise ConnectionError("pasajero")

    await bus._handle("t", "s", DLQ, received(b"{}"), broken, ack_deadline_s=60)
    await _spin()

    assert calls[-1] == "deadline a1=0"  # El nack (plazo 0) es lo último.


async def test_a_hung_handler_is_not_held_beyond_the_max_lease() -> None:
    ticks = iter(range(0, 100_000, 1000))
    bus, calls = make_lease_bus(max_lease_s=1500, clock=lambda: next(ticks))

    async def hung(message: Message) -> None:
        await _spin()

    await bus._handle("t", "s", DLQ, received(b"{}"), hung, ack_deadline_s=60)

    # t=1000 renueva; t=2000 supera los 1500 s y se deja caducar.
    assert calls == ["deadline a1=60", "ack a1"]


async def test_lease_disabled_with_zero_max_lease() -> None:
    bus, calls = make_lease_bus(max_lease_s=0)

    async def slow(message: Message) -> None:
        await _spin()

    await bus._handle("t", "s", DLQ, received(b"{}"), slow, ack_deadline_s=60)

    assert calls == ["ack a1"]


def _modify_failure(kind: str) -> exceptions.InvalidArgument:
    return _ack_failure(kind)


async def test_permanent_lease_failure_stops_renewing() -> None:
    """Exactly-once: si el `ack_id` ya caducó, renovar no sirve; el mensaje volverá."""
    bus, calls = make_lease_bus([_modify_failure("PERMANENT_FAILURE_INVALID_ACK_ID")])

    async def slow(message: Message) -> None:
        await _spin()

    await bus._handle("t", "s", DLQ, received(b"{}"), slow, ack_deadline_s=60)

    assert calls == ["deadline a1=60", "ack a1"]
    assert (bus.lease_failures, bus.lease_extensions) == (1, 0)


async def test_transient_lease_failure_is_retried_next_period() -> None:
    lost = exceptions.ServiceUnavailable("red")  # type: ignore[no-untyped-call]
    bus, calls = make_lease_bus([lost])

    async def slow(message: Message) -> None:
        await _until(lambda: bus.lease_extensions >= 1)

    await bus._handle("t", "s", DLQ, received(b"{}"), slow, ack_deadline_s=60)

    assert bus.lease_failures == 1
    assert bus.lease_extensions >= 1
    assert calls[-1] == "ack a1"


async def test_subscribe_reads_the_ack_deadline_of_the_subscription() -> None:
    subscription = Subscription(
        ack_deadline_seconds=120, dead_letter_policy=DeadLetterPolicy(dead_letter_topic=DLQ)
    )
    bus, _ = make_bus(subscription)
    seen: list[int] = []

    async def poll(*args: Any) -> None:
        seen.append(args[-1])

    bus._poll = poll  # type: ignore[method-assign,assignment]
    await bus.subscribe("knowledge.documents", _ok)
    await _spin()

    assert seen == [120]


async def test_settings_carry_the_max_lease() -> None:
    settings = PubSubSettings(emulator_host="127.0.0.1:8085", max_lease_s=900)
    bus = PubSubMessageBus.from_settings(settings, GcpSettings(), project="")

    assert bus._max_lease_s == 900
    await bus.close()

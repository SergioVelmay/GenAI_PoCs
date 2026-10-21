"""Outbox, relay y alta idempotente (fase 10.5), sin base de datos ni broker."""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import Any

import pytest

from headache_core.adapters.memory import (
    InMemoryCaseIntakeStore,
    InMemoryMessageBus,
    InMemoryOutbox,
)
from headache_core.application.case_intake import (
    CaseIntakeService,
    CaseRequest,
    InvalidIdempotencyKeyError,
)
from headache_core.application.outbox import OutboxRelay
from headache_core.ports import ConflictError, Message
from headache_core.ports.outbox import OutboxEvent


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now


class FlakyBus(InMemoryMessageBus):
    """Falla con los temas de `broken` (broker caído para esa cola)."""

    def __init__(self, broken: set[str]) -> None:
        super().__init__()
        self.broken = broken

    async def publish(self, topic: str, payload: Mapping[str, Any]) -> Message:
        if topic in self.broken:
            raise ConnectionError(f"{topic}: broker no disponible")
        return await super().publish(topic, payload)


async def test_relay_publishes_in_order_and_marks_published() -> None:
    outbox, bus = InMemoryOutbox(), InMemoryMessageBus()
    await outbox.append([OutboxEvent("a", {"n": i}) for i in range(5)])

    assert await OutboxRelay(outbox, bus, batch_size=2).drain() == 5

    assert [m.payload["n"] for m in bus.published] == [0, 1, 2, 3, 4]
    assert await outbox.backlog() == 0


async def test_a_failing_event_is_retried_with_backoff_and_does_not_block_the_rest() -> None:
    time = FakeTime()
    outbox, bus = InMemoryOutbox(clock=time.clock), FlakyBus({"roto"})
    await outbox.append([OutboxEvent("roto", {}), OutboxEvent("ok", {})])
    relay = OutboxRelay(outbox, bus)

    assert await relay.run_once() == 1
    assert [m.topic for m in bus.published] == ["ok"]
    assert await outbox.claim(limit=10, lease_seconds=30) == []  # En espera (backoff de 1 s).

    time.now, bus.broken = 1.0, set()
    assert await relay.run_once() == 1
    assert (relay.published, relay.failed) == (2, 1)
    assert "broker no disponible" in next(iter(outbox.errors.values()))


async def test_backoff_grows_with_attempts_up_to_the_cap() -> None:
    time = FakeTime()
    outbox, bus = InMemoryOutbox(clock=time.clock), FlakyBus({"roto"})
    await outbox.append([OutboxEvent("roto", {})])
    relay = OutboxRelay(outbox, bus, max_backoff_seconds=4)
    waits = []
    for _ in range(5):
        before = time.now
        await relay.run_once()
        [(_, _, available_at, _)] = outbox.events.values()
        waits.append(available_at - before)
        time.now = available_at

    assert waits == [1, 2, 4, 4, 4]


async def test_an_expired_lease_makes_the_event_available_again() -> None:
    time = FakeTime()
    outbox = InMemoryOutbox(clock=time.clock)
    await outbox.append([OutboxEvent("a", {})])

    [claimed] = await outbox.claim(limit=10, lease_seconds=30)  # Y el relay muere.
    assert await outbox.claim(limit=10, lease_seconds=30) == []
    time.now = 31

    [again] = await outbox.claim(limit=10, lease_seconds=30)
    assert (again.id, again.attempts) == (claimed.id, 2)


async def test_topic_prefix_isolates_relays() -> None:
    outbox, bus = InMemoryOutbox(), InMemoryMessageBus()
    await outbox.append([OutboxEvent("pro.a", {}), OutboxEvent("test.a", {})])

    assert await OutboxRelay(outbox, bus, topic_prefix="test.").drain() == 1
    assert await outbox.backlog(topic_prefix="pro.") == 1


def _intake(outbox: InMemoryOutbox) -> CaseIntakeService:
    ids = itertools.count(1)
    return CaseIntakeService(InMemoryCaseIntakeStore(outbox), new_id=lambda: f"a{next(ids)}")


REQUEST = CaseRequest("Cambio de fecha", "Quiero cambiar la fecha de mi pedido.", "ord_9")


async def test_submission_writes_the_email_event_with_its_correlation_id() -> None:
    outbox = InMemoryOutbox()

    submitted = await _intake(outbox).submit(
        customer_id="cus_1", idempotency_key="form-123456", request=REQUEST
    )

    assert (submitted.case_id, submitted.created) == ("case_web_a1", True)
    [(event, *_)] = outbox.events.values()
    assert event.topic == "support.emails"
    assert event.payload["id"] == "web_a1"
    assert event.payload["language"] == "es"
    assert event.payload["order_id"] == "ord_9"
    assert event.payload["correlation_id"] == "case_web_a1"


async def test_same_key_same_body_is_the_same_case_and_other_body_conflicts() -> None:
    outbox = InMemoryOutbox()
    intake = _intake(outbox)
    first = await intake.submit(customer_id="cus_1", idempotency_key="form-123456", request=REQUEST)

    again = await intake.submit(customer_id="cus_1", idempotency_key="form-123456", request=REQUEST)
    other_customer = await intake.submit(
        customer_id="cus_2", idempotency_key="form-123456", request=REQUEST
    )

    assert (again.case_id, again.created) == (first.case_id, False)
    assert other_customer.case_id != first.case_id
    assert await outbox.backlog() == 2
    with pytest.raises(ConflictError):
        await intake.submit(
            customer_id="cus_1",
            idempotency_key="form-123456",
            request=CaseRequest("Otro", "Otro cuerpo.", None),
        )


@pytest.mark.parametrize("key", ["corta", "con espacios 123", "x" * 201, "ñandú-12345"])
async def test_idempotency_keys_are_validated(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKeyError):
        await _intake(InMemoryOutbox()).submit(
            customer_id="cus_1", idempotency_key=key, request=REQUEST
        )

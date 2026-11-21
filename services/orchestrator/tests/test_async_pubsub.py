"""Flujo asíncrono con el emulador de Pub/Sub y PostgreSQL reales (9.G3): exactly-once, duplicados,
DLQ y CAS. Espejo de `test_async_rabbitmq.py` con `ORCHESTRATOR__BROKER=pubsub`.

docker compose up -d ; docker compose --profile pubsub up -d
uv run alembic -c packages/genaipocs_core/alembic.ini upgrade head
uv run pytest -m integration services/orchestrator/tests/test_async_pubsub.py
"""

from __future__ import annotations

import asyncio
import json
import socket
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from genaipocs_core.adapters.postgres import create_engine, create_session_factory
from genaipocs_core.adapters.postgres.cases import PostgresCaseStore
from genaipocs_core.adapters.postgres.outbox import PostgresOutboxStore
from genaipocs_core.adapters.postgres.tables import OutboxEventRow, SupportCaseRow
from genaipocs_core.adapters.pubsub import LOCAL_PROJECT, PubSubMessageBus, subscription_path
from genaipocs_core.application.orchestration import TriageAgent, email_to_payload
from genaipocs_core.application.outbox import OutboxRelay
from genaipocs_core.config import GcpSettings, PubSubSettings, get_settings
from genaipocs_core.domain import CaseStatus
from genaipocs_core.ports import ToolCall
from genaipocs_core.ports.cases import CaseRecord
from orchestrator.async_flow import CaseReviewService, CaseTopics, subscribe_workers
from orchestrator.specialists import build_approval_service, build_specialists
from orchestrator.testing import ORDER, local_world, support_email

pytestmark = pytest.mark.integration

EMULATOR_HOST = get_settings().pubsub.emulator_host or "127.0.0.1:8085"
PENDING = "Tu solicitud queda pendiente de revisión por un agente de Genaipocs."


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(get_settings().postgres, application_name="integration-tests")
    yield create_session_factory(engine)
    async with engine.begin() as connection:
        await connection.execute(delete(SupportCaseRow).where(SupportCaseRow.id.like("case_ps_%")))
        await connection.execute(delete(OutboxEventRow).where(OutboxEventRow.topic.like("ps.%")))
    await engine.dispose()


@pytest.fixture
async def bus() -> AsyncIterator[PubSubMessageBus]:
    host, _, port = EMULATOR_HOST.rpartition(":")
    try:
        socket.create_connection((host, int(port)), timeout=0.5).close()
    except OSError:
        pytest.skip(f"El emulador de Pub/Sub no está arrancado en {EMULATOR_HOST}")
    # Como `pubsub.tf` para el orquestador: exactly-once en las suscripciones de los workers.
    settings = PubSubSettings(emulator_host=EMULATOR_HOST, exactly_once=True)
    bus = PubSubMessageBus.from_settings(settings, GcpSettings(), project=LOCAL_PROJECT)
    yield bus
    await bus.close()


async def _wait_for(store: PostgresCaseStore, case_id: str, status: CaseStatus) -> CaseRecord:
    for _ in range(150):
        record = await store.get(case_id)
        if record is not None and record.status is status:
            return record
        await asyncio.sleep(0.1)
    raise AssertionError(f"{case_id} no llegó a {status}")


async def _dead_letters(bus: PubSubMessageBus, topic: str) -> int:
    subscriber = bus._subscriber
    response = await subscriber.pull(
        # El dead-letter topic `<tema>.dlq` tiene su suscripción `<tema>.dlq`.
        subscription=subscription_path(LOCAL_PROJECT, topic, "dlq"),
        max_messages=10,
        timeout=5,
    )
    return len(response.received_messages)


async def test_email_through_pubsub_with_exactly_once_duplicates_and_poison(
    factory: async_sessionmaker[AsyncSession], bus: PubSubMessageBus
) -> None:
    cases = PostgresCaseStore(factory)
    topics = CaseTopics(prefix=f"ps.{uuid.uuid4().hex[:8]}.")
    relay = OutboxRelay(PostgresOutboxStore(factory), bus, topic_prefix=topics.prefix)
    stop = asyncio.Event()
    relay_task = asyncio.create_task(relay.run(stop, idle_seconds=0.05))
    email_id = f"ps_{uuid.uuid4().hex[:10]}"
    triage = {
        "intent": "refund_request",
        "language": "es",
        "order_id": ORDER,
        "urgency": "normal",
        "summary": "Pide el reembolso.",
    }
    refund = ToolCall("c1", "request_refund", json.dumps({"order_id": ORDER, "reason": "No va."}))
    world = local_world([json.dumps(triage), [refund], PENDING])
    await subscribe_workers(
        bus,
        cases,
        triage=TriageAgent(world.llm),
        specialists=build_specialists(world.deps),
        topics=topics,
    )
    config = await bus._subscriber.get_subscription(
        subscription=subscription_path(LOCAL_PROJECT, topics.emails, "worker")
    )
    assert config.enable_exactly_once_delivery is True
    email = support_email("Devolvedme el dinero.", email_id=email_id)

    # El mismo email publicado dos veces (otro `message_id`: exactly-once NO lo deduplica) y un
    # mensaje venenoso.
    await bus.publish(topics.emails, email_to_payload(email))
    await bus.publish(topics.emails, email_to_payload(email))
    await bus.publish(topics.emails, {"id": "roto"})

    case = await _wait_for(cases, f"case_{email_id}", CaseStatus.PENDING_APPROVAL)
    await asyncio.sleep(0.5)  # Margen para que el duplicado se procese (y se descarte).
    # Triaje + tool + respuesta: una sola vez. La deduplicación la hace el CaseStore.
    assert len(world.llm.calls) == 3
    assert await _dead_letters(bus, topics.emails) == 1

    review = CaseReviewService(cases, build_approval_service(world.deps), topics=topics)
    reviewed = await review.review(case.case_id, reviewer="ops-1", approve=True)
    assert reviewed.status is CaseStatus.RESOLVED
    for _ in range(50):  # El resultado de la revisión también sale por el outbox.
        if await relay.drain() == 0 and relay.published >= 3:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await relay_task
    # Traspaso, resultado pendiente y resultado resuelto: una vez cada uno.
    assert relay.published == 3
    assert bus.ack_failures == 0


async def test_a_second_ack_is_rejected_by_exactly_once_and_counted(
    bus: PubSubMessageBus,
) -> None:
    """El error real del emulador tiene la forma que espera `_settle` (motivo y metadatos)."""
    topic = f"ps.{uuid.uuid4().hex[:8]}.probe"
    await bus.publish(topic, {"x": 1})
    subscription = subscription_path(LOCAL_PROJECT, topic, "worker")
    response = await bus._subscriber.pull(subscription=subscription, max_messages=1, timeout=10)
    [raw] = response.received_messages

    await bus._ack(subscription, raw, "m1")
    await bus._ack(subscription, raw, "m1")

    assert bus.ack_failures == 1

"""Flujo asíncrono con RabbitMQ y PostgreSQL reales (fase 9.6): duplicados, DLQ y CAS.

docker compose up -d
uv run alembic -c packages/headache_core/alembic.ini upgrade head
uv run pytest -m integration services/orchestrator
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

import aio_pika
import pytest
from sqlalchemy import delete

from headache_core.adapters.postgres import create_engine, create_session_factory
from headache_core.adapters.postgres.cases import PostgresCaseStore
from headache_core.adapters.postgres.tables import SupportCaseRow
from headache_core.adapters.rabbitmq import RabbitMQMessageBus
from headache_core.application.orchestration import TriageAgent, email_to_payload
from headache_core.config import get_settings
from headache_core.domain import CaseStatus, Specialist
from headache_core.ports import ToolCall
from headache_core.ports.cases import CaseRecord
from orchestrator.async_flow import CaseReviewService, CaseTopics, subscribe_workers
from orchestrator.specialists import build_approval_service, build_specialists
from orchestrator.testing import ORDER, local_world, support_email

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
PENDING = "Tu solicitud queda pendiente de revisión por un agente de Headache."


@pytest.fixture
async def cases() -> AsyncIterator[PostgresCaseStore]:
    engine = create_engine(get_settings().postgres, application_name="integration-tests")
    store = PostgresCaseStore(create_session_factory(engine))
    yield store
    async with engine.begin() as connection:
        await connection.execute(delete(SupportCaseRow).where(SupportCaseRow.id.like("case_it_%")))
    await engine.dispose()


@pytest.fixture
async def bus() -> AsyncIterator[RabbitMQMessageBus]:
    bus = await RabbitMQMessageBus.connect(
        get_settings().rabbitmq, max_deliveries=2, retry_delay_ms=50
    )
    yield bus
    await bus.close()


def _record(case_id: str, email_id: str) -> CaseRecord:
    return CaseRecord(
        case_id, email_id, "cus_1", Specialist.KNOWLEDGE, CaseStatus.TRIAGED, {"x": 1}, NOW, NOW
    )


async def test_case_store_guarantees_one_case_per_email_and_atomic_transitions(
    cases: PostgresCaseStore,
) -> None:
    email_id = f"it_{uuid.uuid4().hex[:10]}"
    record = _record(f"case_{email_id}", email_id)

    assert await cases.create(record) is True
    assert await cases.create(record) is False
    assert await cases.create(replace(record, case_id=f"case_{email_id}_b")) is False

    resolved = replace(record, status=CaseStatus.RESOLVED, outcome={"status": "resolved"})
    first, second = await asyncio.gather(
        cases.transition(record.case_id, CaseStatus.TRIAGED, resolved),
        cases.transition(record.case_id, CaseStatus.TRIAGED, resolved),
    )
    assert sorted([first, second]) == [False, True]
    stored = await cases.get(record.case_id)
    assert stored is not None and stored.outcome == {"status": "resolved"}


async def _wait_for(store: PostgresCaseStore, case_id: str, status: CaseStatus) -> CaseRecord:
    for _ in range(100):
        record = await store.get(case_id)
        if record is not None and record.status is status:
            return record
        await asyncio.sleep(0.1)
    raise AssertionError(f"{case_id} no llegó a {status}")


async def _dlq_count(queue: str) -> int:
    connection = await aio_pika.connect(get_settings().rabbitmq.url())
    async with connection:
        declared = await (await connection.channel()).declare_queue(queue, passive=True)
        return declared.declaration_result.message_count or 0


async def test_email_through_rabbitmq_with_duplicates_and_poison_messages(
    cases: PostgresCaseStore, bus: RabbitMQMessageBus
) -> None:
    topics = CaseTopics(prefix=f"it.{uuid.uuid4().hex[:8]}.")
    email_id = f"it_{uuid.uuid4().hex[:10]}"
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
    email = support_email("Devolvedme el dinero.", email_id=email_id)

    # El mismo email dos veces (entrega at-least-once del buzón) y un mensaje venenoso.
    await bus.publish(topics.emails, email_to_payload(email))
    await bus.publish(topics.emails, email_to_payload(email))
    await bus.publish(topics.emails, {"id": "roto"})

    case = await _wait_for(cases, f"case_{email_id}", CaseStatus.PENDING_APPROVAL)
    await asyncio.sleep(0.5)  # Margen para que el duplicado se procese (y se descarte).
    assert len(world.llm.calls) == 3  # Triaje + tool + respuesta: una sola vez.
    assert await _dlq_count(f"{topics.emails}.dlq") == 1

    review = CaseReviewService(cases, build_approval_service(world.deps), bus, topics=topics)
    reviewed = await review.review(case.case_id, reviewer="ops-1", approve=True)
    assert reviewed.status is CaseStatus.RESOLVED

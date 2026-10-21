"""Outbox y altas idempotentes en PostgreSQL real (fase 10.5).

docker compose up -d
uv run alembic -c packages/headache_core/alembic.ini upgrade head
uv run pytest -m integration -k outbox
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from headache_core.adapters.postgres import create_engine, create_session_factory
from headache_core.adapters.postgres.cases import PostgresCaseStore
from headache_core.adapters.postgres.outbox import (
    PostgresCaseIntakeStore,
    PostgresOutboxStore,
    add_events,
)
from headache_core.adapters.postgres.tables import (
    CaseSubmissionRow,
    OutboxEventRow,
    SupportCaseRow,
)
from headache_core.application.case_intake import CaseIntakeService, CaseRequest
from headache_core.config import get_settings
from headache_core.domain import CaseStatus, Specialist
from headache_core.ports import ConflictError
from headache_core.ports.cases import CaseRecord
from headache_core.ports.outbox import OutboxEvent

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine(get_settings().postgres, application_name="integration-tests")
    yield engine
    await engine.dispose()


@pytest.fixture
def prefix() -> str:
    # Cada test con sus temas: el relay de un test no toca filas de otro (ni de desarrollo).
    return f"it.{uuid.uuid4().hex[:8]}."


@pytest.fixture
async def factory(
    engine: AsyncEngine, prefix: str
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    yield create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.execute(delete(OutboxEventRow).where(OutboxEventRow.topic.like(f"{prefix}%")))
        await conn.execute(delete(SupportCaseRow).where(SupportCaseRow.id.like("case_it_%")))
        await conn.execute(
            delete(CaseSubmissionRow).where(CaseSubmissionRow.customer_id.like("cus_it%"))
        )


async def _events(factory: async_sessionmaker[AsyncSession], prefix: str, n: int) -> None:
    async with factory() as session, session.begin():
        await add_events(session, [OutboxEvent(f"{prefix}t", {"n": i}) for i in range(n)])


async def test_event_is_written_only_if_the_case_transition_applies(
    factory: async_sessionmaker[AsyncSession], prefix: str
) -> None:
    cases, outbox = PostgresCaseStore(factory), PostgresOutboxStore(factory)
    email_id = f"it_{uuid.uuid4().hex[:10]}"
    record = CaseRecord(
        f"case_{email_id}",
        email_id,
        "cus_it",
        Specialist.KNOWLEDGE,
        CaseStatus.TRIAGED,
        {},
        NOW,
        NOW,
    )
    await cases.create(record)
    resolved = replace(record, status=CaseStatus.RESOLVED)
    event = OutboxEvent(f"{prefix}case.outcomes", {"case_id": record.case_id})

    results = await asyncio.gather(
        *(
            cases.transition(record.case_id, CaseStatus.TRIAGED, resolved, events=[event])
            for _ in range(5)
        )
    )

    assert sorted(results) == [False, False, False, False, True]
    assert await outbox.backlog(topic_prefix=prefix) == 1


async def test_concurrent_relays_never_claim_the_same_event(
    factory: async_sessionmaker[AsyncSession], prefix: str
) -> None:
    outbox = PostgresOutboxStore(factory)
    await _events(factory, prefix, 60)

    batches = await asyncio.gather(
        *(outbox.claim(limit=25, lease_seconds=30, topic_prefix=prefix) for _ in range(4))
    )

    ids = [event.id for batch in batches for event in batch]
    assert len(ids) == 60 and len(set(ids)) == 60  # SKIP LOCKED: sin solapes ni pérdidas.
    assert all(batch == sorted(batch, key=lambda e: e.id) for batch in batches)


async def test_lease_expires_and_failed_events_come_back_later(
    factory: async_sessionmaker[AsyncSession], prefix: str
) -> None:
    outbox = PostgresOutboxStore(factory)
    await _events(factory, prefix, 2)

    first, second = await outbox.claim(limit=10, lease_seconds=0.3, topic_prefix=prefix)
    assert await outbox.claim(limit=10, lease_seconds=0.3, topic_prefix=prefix) == []
    await outbox.mark_published([first.id])
    await outbox.release(second.id, error="broker caído", retry_in_seconds=0.6)

    await asyncio.sleep(0.4)
    assert await outbox.claim(limit=10, lease_seconds=1, topic_prefix=prefix) == []
    await asyncio.sleep(0.4)
    [again] = await outbox.claim(limit=10, lease_seconds=1, topic_prefix=prefix)
    assert (again.id, again.attempts) == (second.id, 2)
    assert await outbox.backlog(topic_prefix=prefix) == 1


async def test_relay_crash_after_claim_does_not_lose_events(
    factory: async_sessionmaker[AsyncSession], prefix: str
) -> None:
    outbox = PostgresOutboxStore(factory)
    await _events(factory, prefix, 3)

    await outbox.claim(limit=10, lease_seconds=0.2, topic_prefix=prefix)  # Y el relay muere.
    await asyncio.sleep(0.3)

    recovered = await outbox.claim(limit=10, lease_seconds=30, topic_prefix=prefix)
    assert len(recovered) == 3


async def test_idempotent_submission(
    factory: async_sessionmaker[AsyncSession], prefix: str
) -> None:
    store = PostgresCaseIntakeStore(factory)
    intake = CaseIntakeService(store, topic=f"{prefix}support.emails")
    customer = f"cus_it{uuid.uuid4().hex[:8]}"
    request = CaseRequest("No puedo ir", "Quiero cambiar la fecha del pedido.", "ord_1")
    key = f"key-{uuid.uuid4().hex}"

    results = await asyncio.gather(
        *(
            intake.submit(customer_id=customer, idempotency_key=key, request=request)
            for _ in range(5)
        )
    )
    other_customer = await intake.submit(
        customer_id=f"{customer}b", idempotency_key=key, request=request
    )

    assert len({r.case_id for r in results}) == 1
    assert sum(r.created for r in results) == 1
    assert other_customer.case_id != results[0].case_id
    assert await PostgresOutboxStore(factory).backlog(topic_prefix=prefix) == 2
    with pytest.raises(ConflictError):
        await intake.submit(
            customer_id=customer,
            idempotency_key=key,
            request=replace(request, body="Otra cosa distinta."),
        )
    stored = await store.get_submission(results[0].case_id)
    assert stored is not None and stored.customer_id == customer

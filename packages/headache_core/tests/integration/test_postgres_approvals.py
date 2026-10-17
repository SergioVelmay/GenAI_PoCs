"""Aprobaciones en PostgreSQL: la base de datos garantiza unicidad y decisiones atómicas.

docker compose up -d
uv run alembic -c packages/headache_core/alembic.ini upgrade head
uv run pytest -m integration
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine

from headache_core.adapters.postgres import create_engine, create_session_factory
from headache_core.adapters.postgres.approvals import PostgresApprovalStore
from headache_core.adapters.postgres.tables import ApprovalRow
from headache_core.config import get_settings
from headache_core.ports.approvals import ApprovalConflictError, ApprovalRequest, ApprovalStatus

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine(get_settings().postgres, application_name="integration-tests")
    yield engine
    await engine.dispose()


@pytest.fixture
async def store(engine: AsyncEngine) -> AsyncIterator[PostgresApprovalStore]:
    yield PostgresApprovalStore(create_session_factory(engine))
    async with engine.begin() as conn:
        await conn.execute(delete(ApprovalRow).where(ApprovalRow.customer_id.like("cus_it%")))


def _request(customer_id: str, approval_id: str | None = None) -> ApprovalRequest:
    return ApprovalRequest(
        id=approval_id or f"apr_it{uuid.uuid4().hex[:12]}",
        tool="request_refund",
        arguments='{"order_id":"ord_1","reason":"No voy."}',
        customer_id=customer_id,
        summary="Reembolso del pedido ord_1.",
        reason="Reembolso: mueve dinero.",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=24),
    )


async def test_roundtrip_and_reviewer_queue(store: PostgresApprovalStore) -> None:
    customer = f"cus_it{uuid.uuid4().hex[:8]}"
    request = _request(customer)

    await store.add(request)

    assert await store.get(request.id) == request
    assert await store.find_open(request.fingerprint) == request
    pending = await store.list_requests(status=ApprovalStatus.PENDING, customer_id=customer)
    assert pending == [request]


async def test_only_one_open_request_per_action(store: PostgresApprovalStore) -> None:
    customer = f"cus_it{uuid.uuid4().hex[:8]}"
    await store.add(_request(customer))

    with pytest.raises(ApprovalConflictError):
        await store.add(_request(customer))


async def test_concurrent_decisions_only_one_wins(store: PostgresApprovalStore) -> None:
    request = _request(f"cus_it{uuid.uuid4().hex[:8]}")
    await store.add(request)

    def approved_by(reviewer: str) -> ApprovalRequest:
        return replace(request, status=ApprovalStatus.APPROVED, reviewer=reviewer, decided_at=NOW)

    results = await asyncio.gather(
        *(
            store.transition(request.id, ApprovalStatus.PENDING, approved_by(f"ops-{i}"))
            for i in range(5)
        )
    )

    assert sorted(results) == [False] * 4 + [True]
    stored = await store.get(request.id)
    assert stored is not None and stored.status is ApprovalStatus.APPROVED
    # Cerrada la solicitud (ejecutada), la misma acción puede volver a pedirse.
    executed = replace(stored, status=ApprovalStatus.EXECUTED, result="{}")
    assert await store.transition(request.id, ApprovalStatus.APPROVED, executed)
    assert await store.find_open(request.fingerprint) is None

"""Casos de soporte en PostgreSQL: las garantías de idempotencia las da la base de datos.

- `INSERT ... ON CONFLICT DO NOTHING`: un solo caso por email aunque dos réplicas lo creen.
- `UPDATE ... WHERE status = esperado`: un caso no se cierra dos veces ni se reabre.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from headache_core.adapters.postgres.outbox import add_events
from headache_core.adapters.postgres.tables import SupportCaseRow
from headache_core.domain import CaseStatus, Specialist
from headache_core.ports.cases import CaseRecord
from headache_core.ports.outbox import OutboxEvent


class PostgresCaseStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def get(self, case_id: str) -> CaseRecord | None:
        async with self._factory() as session:
            row = await session.get(SupportCaseRow, case_id)
            return _to_record(row) if row else None

    async def create(self, record: CaseRecord, *, events: Sequence[OutboxEvent] = ()) -> bool:
        stmt = (
            insert(SupportCaseRow)
            .values(**_values(record), id=record.case_id, created_at=record.created_at)
            .on_conflict_do_nothing()
            .returning(SupportCaseRow.id)
        )
        async with self._factory() as session, session.begin():
            created = (await session.execute(stmt)).scalar_one_or_none() is not None
            if created:
                await add_events(session, events)
            return created

    async def transition(
        self,
        case_id: str,
        expected: CaseStatus,
        updated: CaseRecord,
        *,
        events: Sequence[OutboxEvent] = (),
    ) -> bool:
        stmt = (
            update(SupportCaseRow)
            .where(SupportCaseRow.id == case_id, SupportCaseRow.status == expected)
            .values(**_values(updated))
            .returning(SupportCaseRow.id)
        )
        async with self._factory() as session, session.begin():
            applied = (await session.execute(stmt)).scalar_one_or_none() is not None
            # Mismo COMMIT que el cambio de estado: o se guardan los dos o ninguno.
            if applied:
                await add_events(session, events)
            return applied

    async def list_cases(
        self, *, status: CaseStatus | None = None, limit: int = 50
    ) -> list[CaseRecord]:
        stmt = select(SupportCaseRow).order_by(SupportCaseRow.created_at).limit(limit)
        if status is not None:
            stmt = stmt.where(SupportCaseRow.status == status)
        async with self._factory() as session:
            return [_to_record(row) for row in await session.scalars(stmt)]


def _values(record: CaseRecord) -> dict[str, object]:
    return {
        "email_id": record.email_id,
        "customer_id": record.customer_id,
        "specialist": record.specialist,
        "status": record.status,
        "envelope": dict(record.envelope) if record.envelope is not None else None,
        "outcome": dict(record.outcome) if record.outcome is not None else None,
        "approval_id": record.approval_id,
        "guardrails": list(record.guardrails),
        "updated_at": record.updated_at,
    }


def _to_record(row: SupportCaseRow) -> CaseRecord:
    return CaseRecord(
        case_id=row.id,
        email_id=row.email_id,
        customer_id=row.customer_id,
        specialist=Specialist(row.specialist) if row.specialist else None,
        status=CaseStatus(row.status),
        envelope=row.envelope,
        created_at=row.created_at,
        updated_at=row.updated_at,
        outcome=row.outcome,
        approval_id=row.approval_id,
        guardrails=tuple(row.guardrails),
    )

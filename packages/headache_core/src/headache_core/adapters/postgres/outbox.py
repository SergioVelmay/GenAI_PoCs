"""Outbox y altas idempotentes en PostgreSQL (fase 10.5).

- `claim`: `UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)`. Cada relay se lleva
  filas distintas sin esperar a los demás; la reserva es mover `available_at` al futuro, así
  que si el relay cae, las filas vuelven solas al vencer.
- `submit`: `INSERT ... ON CONFLICT DO NOTHING` sobre (cliente, clave). Si entra, el evento se
  inserta en la misma transacción; si no, se compara la huella del cuerpo.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from headache_core.adapters.postgres.tables import CaseSubmissionRow, OutboxEventRow
from headache_core.ports import ConflictError
from headache_core.ports.intake import CaseSubmission
from headache_core.ports.outbox import OutboxEvent, PendingEvent


async def add_events(session: AsyncSession, events: Sequence[OutboxEvent]) -> None:
    """Inserta eventos en la transacción en curso (la del cambio de estado)."""
    if events:
        await session.execute(
            insert(OutboxEventRow),
            [{"topic": event.topic, "payload": dict(event.payload)} for event in events],
        )


class PostgresOutboxStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def claim(
        self, *, limit: int, lease_seconds: float, topic_prefix: str = ""
    ) -> list[PendingEvent]:
        candidates = (
            select(OutboxEventRow.id)
            .where(
                OutboxEventRow.published_at.is_(None),
                OutboxEventRow.available_at <= func.now(),
                OutboxEventRow.topic.startswith(topic_prefix, autoescape=True),
            )
            .order_by(OutboxEventRow.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        stmt = (
            update(OutboxEventRow)
            .where(OutboxEventRow.id.in_(candidates.scalar_subquery()))
            .values(
                available_at=func.now() + timedelta(seconds=lease_seconds),
                attempts=OutboxEventRow.attempts + 1,
            )
            .returning(
                OutboxEventRow.id,
                OutboxEventRow.topic,
                OutboxEventRow.payload,
                OutboxEventRow.attempts,
            )
        )
        async with self._factory() as session, session.begin():
            rows = (await session.execute(stmt)).all()
        # RETURNING no garantiza orden: se publica por orden de inserción.
        return sorted(
            (PendingEvent(row.id, row.topic, row.payload, row.attempts) for row in rows),
            key=lambda event: event.id,
        )

    async def mark_published(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        stmt = (
            update(OutboxEventRow)
            .where(OutboxEventRow.id.in_(list(ids)))
            .values(published_at=func.now(), last_error=None)
        )
        async with self._factory() as session, session.begin():
            await session.execute(stmt)

    async def release(self, event_id: int, *, error: str, retry_in_seconds: float) -> None:
        stmt = (
            update(OutboxEventRow)
            .where(OutboxEventRow.id == event_id)
            .values(
                available_at=func.now() + timedelta(seconds=retry_in_seconds),
                last_error=error[:2000],
            )
        )
        async with self._factory() as session, session.begin():
            await session.execute(stmt)

    async def backlog(self, *, topic_prefix: str = "") -> int:
        stmt = select(func.count()).where(
            OutboxEventRow.published_at.is_(None),
            OutboxEventRow.topic.startswith(topic_prefix, autoescape=True),
        )
        async with self._factory() as session:
            return int((await session.execute(stmt)).scalar_one())


class PostgresCaseIntakeStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def submit(
        self, submission: CaseSubmission, event: OutboxEvent
    ) -> tuple[CaseSubmission, bool]:
        stmt = (
            insert(CaseSubmissionRow)
            .values(
                customer_id=submission.customer_id,
                idempotency_key=submission.idempotency_key,
                request_hash=submission.request_hash,
                case_id=submission.case_id,
                email_id=submission.email_id,
                created_at=submission.created_at,
            )
            .on_conflict_do_nothing(index_elements=["customer_id", "idempotency_key"])
            .returning(CaseSubmissionRow.case_id)
        )
        async with self._factory() as session, session.begin():
            if (await session.execute(stmt)).scalar_one_or_none() is not None:
                await add_events(session, [event])
                return submission, True
            row = await session.get(
                CaseSubmissionRow, (submission.customer_id, submission.idempotency_key)
            )
        if row is None:  # Borrada entre medias: no debería pasar.
            raise RuntimeError("Alta en conflicto pero no encontrada.")
        existing = _to_submission(row)
        if existing.request_hash != submission.request_hash:
            raise ConflictError("Idempotency-Key reutilizada con otro cuerpo.")
        return existing, False

    async def get_submission(self, case_id: str) -> CaseSubmission | None:
        stmt = select(CaseSubmissionRow).where(CaseSubmissionRow.case_id == case_id)
        async with self._factory() as session:
            row = (await session.scalars(stmt)).first()
            return _to_submission(row) if row else None


def _to_submission(row: CaseSubmissionRow) -> CaseSubmission:
    return CaseSubmission(
        customer_id=row.customer_id,
        idempotency_key=row.idempotency_key,
        request_hash=row.request_hash,
        case_id=row.case_id,
        email_id=row.email_id,
        created_at=row.created_at,
    )

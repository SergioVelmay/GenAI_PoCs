"""Solicitudes de aprobación en PostgreSQL: sobreviven a reinicios y las comparten las réplicas.

Las dos garantías que importan las da la base de datos, no el código:
- índice único parcial: una sola solicitud abierta por acción (`ApprovalConflictError`);
- `UPDATE ... WHERE status = esperado`: dos revisores no pueden aprobar (y ejecutar) dos veces.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from headache_core.adapters.postgres.tables import ApprovalRow
from headache_core.ports.approvals import (
    OPEN_STATUSES,
    ApprovalConflictError,
    ApprovalRequest,
    ApprovalStatus,
)


class PostgresApprovalStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def add(self, request: ApprovalRequest) -> None:
        try:
            async with self._factory() as session, session.begin():
                session.add(_to_row(request))
        except IntegrityError as exc:
            raise ApprovalConflictError(
                f"Ya hay una solicitud abierta para {request.tool}."
            ) from exc

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        async with self._factory() as session:
            row = await session.get(ApprovalRow, approval_id)
            return _to_request(row) if row else None

    async def find_open(self, fingerprint: str) -> ApprovalRequest | None:
        stmt = select(ApprovalRow).where(
            ApprovalRow.fingerprint == fingerprint, ApprovalRow.status.in_(OPEN_STATUSES)
        )
        async with self._factory() as session:
            row = (await session.scalars(stmt)).first()
            return _to_request(row) if row else None

    async def list_requests(
        self,
        *,
        status: ApprovalStatus | None = None,
        customer_id: str | None = None,
        limit: int = 50,
    ) -> list[ApprovalRequest]:
        stmt = select(ApprovalRow).order_by(ApprovalRow.created_at).limit(limit)
        if status is not None:
            stmt = stmt.where(ApprovalRow.status == status)
        if customer_id is not None:
            stmt = stmt.where(ApprovalRow.customer_id == customer_id)
        async with self._factory() as session:
            return [_to_request(row) for row in await session.scalars(stmt)]

    async def transition(
        self, approval_id: str, expected: ApprovalStatus, updated: ApprovalRequest
    ) -> bool:
        stmt = (
            update(ApprovalRow)
            .where(ApprovalRow.id == approval_id, ApprovalRow.status == expected)
            .values(
                status=updated.status,
                reviewer=updated.reviewer,
                note=updated.note,
                decided_at=updated.decided_at,
                result=updated.result,
            )
            .returning(ApprovalRow.id)
        )
        async with self._factory() as session, session.begin():
            return (await session.execute(stmt)).scalar_one_or_none() is not None


def _to_row(request: ApprovalRequest) -> ApprovalRow:
    return ApprovalRow(
        id=request.id,
        tool=request.tool,
        arguments=request.arguments,
        fingerprint=request.fingerprint,
        customer_id=request.customer_id,
        summary=request.summary,
        reason=request.reason,
        status=request.status,
        created_at=request.created_at,
        expires_at=request.expires_at,
        decided_at=request.decided_at,
        reviewer=request.reviewer,
        note=request.note,
        result=request.result,
    )


def _to_request(row: ApprovalRow) -> ApprovalRequest:
    return ApprovalRequest(
        id=row.id,
        tool=row.tool,
        arguments=row.arguments,
        customer_id=row.customer_id,
        summary=row.summary,
        reason=row.reason,
        created_at=row.created_at,
        expires_at=row.expires_at,
        status=ApprovalStatus(row.status),
        reviewer=row.reviewer,
        note=row.note,
        decided_at=row.decided_at,
        result=row.result,
    )

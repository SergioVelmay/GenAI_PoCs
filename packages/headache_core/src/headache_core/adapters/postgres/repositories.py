"""Repositorios PostgreSQL (SQLAlchemy async). Traducen filas <-> entidades del dominio."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from headache_core.adapters.postgres.tables import (
    CustomerRow,
    EventRow,
    OrderRow,
    RefundRow,
    SessionRow,
)
from headache_core.domain import (
    Customer,
    Event,
    EventCategory,
    Money,
    Order,
    OrderStatus,
    Refund,
    Session,
)
from headache_core.ports import ConflictError


class PostgresCustomerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def get(self, customer_id: str) -> Customer | None:
        row = await self._db.get(CustomerRow, customer_id)
        if row is None:
            return None
        return Customer(row.id, row.full_name, row.email, row.country, row.language)


class PostgresEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def get(self, event_id: str) -> Event | None:
        row = await self._db.get(EventRow, event_id)
        return _to_event(row) if row else None

    async def find(
        self,
        *,
        city: str | None = None,
        category: EventCategory | None = None,
        limit: int = 20,
    ) -> list[Event]:
        stmt = select(EventRow)
        if city is not None:
            stmt = stmt.where(EventRow.city == city)
        if category is not None:
            stmt = stmt.where(EventRow.category == category)
        rows = await self._db.scalars(stmt.order_by(EventRow.id).limit(limit))
        return [_to_event(r) for r in rows]


class PostgresSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def get(self, session_id: str) -> Session | None:
        row = await self._db.get(SessionRow, session_id)
        return _to_session(row) if row else None

    async def list_for_event(
        self,
        event_id: str,
        *,
        starts_after: datetime | None = None,
        only_available: bool = False,
    ) -> list[Session]:
        stmt = select(SessionRow).where(SessionRow.event_id == event_id)
        if starts_after is not None:
            stmt = stmt.where(SessionRow.starts_at > starts_after)
        if only_available:
            stmt = stmt.where(SessionRow.available > 0)
        rows = await self._db.scalars(stmt.order_by(SessionRow.starts_at))
        return [_to_session(r) for r in rows]

    async def try_reserve(self, session_id: str, quantity: int) -> bool:
        # UPDATE condicional y atómico: la comprobación y el descuento ocurren en una sola
        # sentencia con la fila bloqueada, así que dos compras no pueden leer el mismo stock.
        stmt = (
            update(SessionRow)
            .where(SessionRow.id == session_id, SessionRow.available >= quantity)
            .values(available=SessionRow.available - quantity)
            .returning(SessionRow.id)
        )
        return (await self._db.scalar(stmt)) is not None

    async def release(self, session_id: str, quantity: int) -> None:
        await self._db.execute(
            update(SessionRow)
            .where(SessionRow.id == session_id)
            .values(available=SessionRow.available + quantity)
        )


class PostgresOrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def get(self, order_id: str, *, for_update: bool = False) -> Order | None:
        stmt = select(OrderRow).where(OrderRow.id == order_id)
        if for_update:
            # populate_existing: si la fila ya estaba en la identity map, refresca sus valores.
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        row = await self._db.scalar(stmt)
        return _to_order(row) if row else None

    async def get_by_idempotency_key(self, key: str) -> Order | None:
        row = await self._db.scalar(select(OrderRow).where(OrderRow.idempotency_key == key))
        return _to_order(row) if row else None

    async def add(self, order: Order, *, idempotency_key: str | None = None) -> None:
        self._db.add(
            OrderRow(
                id=order.id,
                customer_id=order.customer_id,
                session_id=order.session_id,
                quantity=order.quantity,
                total_amount=order.total.amount,
                currency=order.total.currency,
                status=order.status,
                created_at=order.created_at,
                idempotency_key=idempotency_key,
            )
        )
        await _flush_or_conflict(self._db, "El pedido ya existe (id o Idempotency-Key).")

    async def update_status(self, order_id: str, status: OrderStatus) -> None:
        stmt = update(OrderRow).where(OrderRow.id == order_id).values(status=status)
        await self._db.execute(stmt)

    async def list_for_customer(self, customer_id: str, *, limit: int = 20) -> list[Order]:
        rows = await self._db.scalars(
            select(OrderRow)
            .where(OrderRow.customer_id == customer_id)
            .order_by(OrderRow.created_at.desc())
            .limit(limit)
        )
        return [_to_order(r) for r in rows]


class PostgresRefundRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def add(self, refund: Refund) -> None:
        self._db.add(
            RefundRow(
                id=refund.id,
                order_id=refund.order_id,
                amount=refund.amount.amount,
                currency=refund.amount.currency,
                reason=refund.reason,
                status=refund.status,
                requested_at=refund.requested_at,
            )
        )
        await _flush_or_conflict(self._db, f"El pedido {refund.order_id} ya tiene un reembolso.")


async def _flush_or_conflict(db: AsyncSession, message: str) -> None:
    try:
        await db.flush()
    except IntegrityError as exc:
        # 23505 = unique_violation. Otras violaciones (FK, CHECK) son errores de programación.
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise ConflictError(message) from exc
        raise


def _to_event(row: EventRow) -> Event:
    return Event(
        id=row.id,
        title=row.title,
        category=row.category,
        city=row.city,
        venue_id=row.venue_id,
        description=row.description,
        price=Money(row.price_amount, row.currency),
        refundable=row.refundable,
        refund_window_hours=row.refund_window_hours,
    )


def _to_session(row: SessionRow) -> Session:
    return Session(row.id, row.event_id, row.starts_at, row.capacity, row.available)


def _to_order(row: OrderRow) -> Order:
    return Order(
        id=row.id,
        customer_id=row.customer_id,
        session_id=row.session_id,
        quantity=row.quantity,
        total=Money(row.total_amount, row.currency),
        status=row.status,
        created_at=row.created_at,
    )

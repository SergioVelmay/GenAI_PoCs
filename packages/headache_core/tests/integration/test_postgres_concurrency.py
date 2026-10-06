"""Tests de integración contra PostgreSQL real: concurrencia, bloqueos e idempotencia.

Cómo ejecutarlos:
    docker compose up -d
    uv run alembic -c packages/headache_core/alembic.ini upgrade head
    uv run pytest -m integration
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from headache_core.adapters.postgres import (
    SqlAlchemyUnitOfWork,
    create_engine,
    create_session_factory,
)
from headache_core.application import PurchaseService, RefundService
from headache_core.config import get_settings
from headache_core.domain import Money, Order, OrderStatus, PurchaseNotAllowedError
from headache_core.ports import ConflictError

pytestmark = pytest.mark.integration

SEATS = 5


@dataclass(frozen=True)
class Fixture:
    customer_id: str
    session_id: str


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine(get_settings().postgres, application_name="integration-tests")
    yield engine
    await engine.dispose()


@pytest.fixture
def factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
async def data(engine: AsyncEngine) -> AsyncIterator[Fixture]:
    """Crea un catálogo aislado (ids it_*) y lo borra al terminar, sin tocar los datos seed."""
    suffix = uuid.uuid4().hex[:12]
    ids = {k: f"{k}_it{suffix}" for k in ("ven", "evt", "ses", "cus")}
    starts_at = datetime.now(UTC) + timedelta(days=10)
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO venues VALUES (:ven, 'Test Hall', 'Madrid', 'ES', 'Calle 1', 100)"),
            ids,
        )
        await conn.execute(
            text(
                "INSERT INTO events VALUES (:evt, 'Test', 'candlelit', 'Madrid', :ven, 'x',"
                " 30.00, 'EUR', true, 48)"
            ),
            ids,
        )
        await conn.execute(
            text("INSERT INTO sessions VALUES (:ses, :evt, :starts_at, 100, :seats)"),
            {**ids, "starts_at": starts_at, "seats": SEATS},
        )
        await conn.execute(
            text(
                "INSERT INTO customers VALUES"
                " (:cus, 'Test User', :cus || '@example.com', 'ES', 'es')"
            ),
            ids,
        )
    yield Fixture(customer_id=ids["cus"], session_id=ids["ses"])
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM refunds WHERE order_id IN"
                " (SELECT id FROM orders WHERE session_id = :ses)"
            ),
            ids,
        )
        for sql in (
            "DELETE FROM orders WHERE session_id = :ses",
            "DELETE FROM sessions WHERE id = :ses",
            "DELETE FROM events WHERE id = :evt",
            "DELETE FROM venues WHERE id = :ven",
            "DELETE FROM customers WHERE id = :cus",
        ):
            await conn.execute(text(sql), ids)


async def _available(engine: AsyncEngine, session_id: str) -> int:
    async with engine.connect() as conn:
        value = await conn.scalar(
            text("SELECT available FROM sessions WHERE id = :id"), {"id": session_id}
        )
    assert isinstance(value, int)
    return value


async def test_concurrent_purchases_never_oversell(
    engine: AsyncEngine, factory: async_sessionmaker[AsyncSession], data: Fixture
) -> None:
    async def buy() -> Order:
        return await PurchaseService(SqlAlchemyUnitOfWork(factory)).purchase(
            customer_id=data.customer_id, session_id=data.session_id, quantity=1
        )

    results = await asyncio.gather(*(buy() for _ in range(20)), return_exceptions=True)

    sold = [r for r in results if isinstance(r, Order)]
    rejected = [r for r in results if isinstance(r, PurchaseNotAllowedError)]
    assert len(sold) == SEATS
    assert len(rejected) == 20 - SEATS
    assert await _available(engine, data.session_id) == 0


async def test_naive_read_modify_write_loses_updates(engine: AsyncEngine, data: Fixture) -> None:
    """Antipatrón: leer, calcular en Python y escribir. Todos leen 5 y todos escriben 4."""
    buyers = 5
    barrier = asyncio.Barrier(buyers)

    async def naive_buy() -> None:
        async with engine.begin() as conn:
            available = await conn.scalar(
                text("SELECT available FROM sessions WHERE id = :id"), {"id": data.session_id}
            )
            await barrier.wait()  # fuerza la carrera: todos han leído antes de que nadie escriba
            await conn.execute(
                text("UPDATE sessions SET available = :value WHERE id = :id"),
                {"value": available - 1, "id": data.session_id},
            )

    await asyncio.gather(*(naive_buy() for _ in range(buyers)))

    # Se "vendieron" 5 entradas, pero el inventario solo bajó en 1: lost update.
    assert await _available(engine, data.session_id) == SEATS - 1


async def test_select_for_update_serializes_buyers(engine: AsyncEngine, data: Fixture) -> None:
    """Bloqueo pesimista: el segundo comprador espera a que el primero confirme."""

    async def locked_buy() -> bool:
        async with engine.begin() as conn:
            available = await conn.scalar(
                text("SELECT available FROM sessions WHERE id = :id FOR UPDATE"),
                {"id": data.session_id},
            )
            assert isinstance(available, int)
            if available < 1:
                return False
            await asyncio.sleep(0.01)  # "lógica de negocio" con la fila bloqueada
            await conn.execute(
                text("UPDATE sessions SET available = :value WHERE id = :id"),
                {"value": available - 1, "id": data.session_id},
            )
            return True

    results = await asyncio.gather(*(locked_buy() for _ in range(10)))

    assert sum(results) == SEATS
    assert await _available(engine, data.session_id) == 0


async def test_concurrent_refunds_approve_only_once(
    engine: AsyncEngine, factory: async_sessionmaker[AsyncSession], data: Fixture
) -> None:
    order = await PurchaseService(SqlAlchemyUnitOfWork(factory)).purchase(
        customer_id=data.customer_id, session_id=data.session_id, quantity=2
    )

    outcomes = await asyncio.gather(
        *(RefundService(SqlAlchemyUnitOfWork(factory)).request_refund(order.id) for _ in range(5))
    )

    assert sum(o.decision.approved for o in outcomes) == 1
    assert await _available(engine, data.session_id) == SEATS


async def test_idempotent_purchase_creates_a_single_order(
    engine: AsyncEngine, factory: async_sessionmaker[AsyncSession], data: Fixture
) -> None:
    async def buy() -> Order:
        return await PurchaseService(SqlAlchemyUnitOfWork(factory)).purchase(
            customer_id=data.customer_id,
            session_id=data.session_id,
            quantity=2,
            idempotency_key=f"key-{data.session_id}",
        )

    first = await buy()
    retry = await buy()

    assert retry.id == first.id
    assert await _available(engine, data.session_id) == SEATS - 2


async def test_duplicate_idempotency_key_maps_to_conflict(
    factory: async_sessionmaker[AsyncSession], data: Fixture
) -> None:
    def order() -> Order:
        return Order(
            id=f"ord_{uuid.uuid4().hex}",
            customer_id=data.customer_id,
            session_id=data.session_id,
            quantity=1,
            total=Money(Decimal("30.00"), "EUR"),
            status=OrderStatus.PAID,
            created_at=datetime.now(UTC),
        )

    key = f"dup-{data.session_id}"
    async with SqlAlchemyUnitOfWork(factory) as uow:
        await uow.orders.add(order(), idempotency_key=key)
        await uow.commit()

    with pytest.raises(ConflictError):
        async with SqlAlchemyUnitOfWork(factory) as uow:
            await uow.orders.add(order(), idempotency_key=key)

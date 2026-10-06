"""Puertos de persistencia del dominio (PostgreSQL en Fase 1, en memoria para tests)."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from headache_core.domain import (
    Customer,
    Event,
    EventCategory,
    Order,
    OrderStatus,
    Refund,
    Session,
)


class ConflictError(Exception):
    """La operación viola una restricción de unicidad (idempotencia, reembolso duplicado...)."""


class CustomerRepository(Protocol):
    async def get(self, customer_id: str) -> Customer | None: ...


class EventRepository(Protocol):
    async def get(self, event_id: str) -> Event | None: ...

    async def find(
        self,
        *,
        city: str | None = None,
        category: EventCategory | None = None,
        limit: int = 20,
    ) -> list[Event]: ...


class SessionRepository(Protocol):
    async def get(self, session_id: str) -> Session | None: ...

    async def list_for_event(
        self,
        event_id: str,
        *,
        starts_after: datetime | None = None,
        only_available: bool = False,
    ) -> list[Session]: ...

    async def try_reserve(self, session_id: str, quantity: int) -> bool:
        """Descuenta plazas de forma atómica. False si no quedan suficientes."""
        ...

    async def release(self, session_id: str, quantity: int) -> None: ...


class OrderRepository(Protocol):
    async def get(self, order_id: str, *, for_update: bool = False) -> Order | None: ...

    async def get_by_idempotency_key(self, key: str) -> Order | None: ...

    async def add(self, order: Order, *, idempotency_key: str | None = None) -> None: ...

    async def update_status(self, order_id: str, status: OrderStatus) -> None: ...

    async def list_for_customer(self, customer_id: str, *, limit: int = 20) -> list[Order]: ...


class RefundRepository(Protocol):
    async def add(self, refund: Refund) -> None: ...


class UnitOfWork(Protocol):
    """Una transacción de negocio: todo lo hecho dentro se confirma o se descarta junto."""

    @property
    def customers(self) -> CustomerRepository: ...

    @property
    def events(self) -> EventRepository: ...

    @property
    def sessions(self) -> SessionRepository: ...

    @property
    def orders(self) -> OrderRepository: ...

    @property
    def refunds(self) -> RefundRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

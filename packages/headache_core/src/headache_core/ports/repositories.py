"""Puertos de persistencia del dominio (PostgreSQL en Fase 1, en memoria para tests)."""

from __future__ import annotations

from typing import Protocol

from headache_core.domain import Event, EventCategory, Order, Session


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

    async def list_for_event(self, event_id: str) -> list[Session]: ...


class OrderRepository(Protocol):
    async def get(self, order_id: str) -> Order | None: ...

    async def save(self, order: Order) -> None: ...

    async def list_for_customer(self, customer_id: str) -> list[Order]: ...

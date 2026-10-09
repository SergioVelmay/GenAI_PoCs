"""Puerto de la API de ticketing tal como la consumen los agentes (HTTP o en proceso)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from headache_core.domain import Event, EventCategory, Money, Order, RefundStatus, Session


class TicketingError(Exception):
    """Error de negocio de la API. El mensaje es seguro para mostrarlo al modelo."""


class TicketingNotFoundError(TicketingError):
    pass


class TicketingRejectedError(TicketingError):
    """La API rechaza la operación (HTTP 409/422): reglas de negocio o datos inválidos."""


class TicketingUnavailableError(TicketingError):
    """Timeout, 5xx o red: pasajero. Una escritura en este estado tiene resultado incierto."""


@dataclass(frozen=True, slots=True)
class RefundResult:
    refund_id: str
    order_id: str
    status: RefundStatus
    approved: bool
    amount: Money
    decision_reason: str
    requested_at: datetime


class TicketingPort(Protocol):
    async def search_events(
        self, *, city: str | None = None, category: EventCategory | None = None, limit: int = 20
    ) -> list[Event]: ...

    async def get_event(self, event_id: str) -> Event: ...

    async def list_sessions(
        self, event_id: str, *, only_available: bool = True
    ) -> list[Session]: ...

    async def get_order(self, order_id: str) -> Order: ...

    async def list_customer_orders(self, customer_id: str, *, limit: int = 20) -> list[Order]: ...

    async def request_refund(self, order_id: str, *, reason: str = "") -> RefundResult: ...

"""TicketingPort en proceso: llama a los repositorios y casos de uso sin HTTP (modo monolito).

Misma semántica que `ticketing_api`; con `InMemoryUnitOfWork` es la base de los tests.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from headache_core.application import NotFoundError, RefundService
from headache_core.domain import Event, EventCategory, Order, Session
from headache_core.ports import (
    ConflictError,
    RefundResult,
    TicketingNotFoundError,
    TicketingRejectedError,
    UnitOfWork,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class UnitOfWorkTicketing:
    def __init__(
        self, uow_factory: Callable[[], UnitOfWork], clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def search_events(
        self, *, city: str | None = None, category: EventCategory | None = None, limit: int = 20
    ) -> list[Event]:
        async with self._uow_factory() as uow:
            return await uow.events.find(city=city, category=category, limit=limit)

    async def get_event(self, event_id: str) -> Event:
        async with self._uow_factory() as uow:
            event = await uow.events.get(event_id)
        if event is None:
            raise TicketingNotFoundError(f"Evento {event_id} no encontrado.")
        return event

    async def list_sessions(self, event_id: str, *, only_available: bool = True) -> list[Session]:
        async with self._uow_factory() as uow:
            return await uow.sessions.list_for_event(
                event_id, starts_after=self._clock(), only_available=only_available
            )

    async def get_order(self, order_id: str) -> Order:
        async with self._uow_factory() as uow:
            order = await uow.orders.get(order_id)
        if order is None:
            raise TicketingNotFoundError(f"Pedido {order_id} no encontrado.")
        return order

    async def list_customer_orders(self, customer_id: str, *, limit: int = 20) -> list[Order]:
        async with self._uow_factory() as uow:
            return await uow.orders.list_for_customer(customer_id, limit=limit)

    async def request_refund(self, order_id: str, *, reason: str = "") -> RefundResult:
        try:
            outcome = await RefundService(self._uow_factory()).request_refund(
                order_id, reason=reason, requested_at=self._clock()
            )
        except NotFoundError as exc:
            raise TicketingNotFoundError(str(exc)) from exc
        except ConflictError as exc:
            raise TicketingRejectedError(str(exc)) from exc
        refund = outcome.refund
        return RefundResult(
            refund_id=refund.id,
            order_id=refund.order_id,
            status=refund.status,
            approved=outcome.decision.approved,
            amount=refund.amount,
            decision_reason=outcome.decision.reason,
            requested_at=refund.requested_at,
        )

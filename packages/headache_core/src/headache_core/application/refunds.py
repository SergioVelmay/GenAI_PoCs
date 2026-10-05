"""Caso de uso: solicitar un reembolso. Orquesta puertos + reglas de dominio; no conoce la nube."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from headache_core.domain import OrderStatus, RefundDecision, evaluate_refund
from headache_core.ports import EventRepository, OrderRepository, SessionRepository


class NotFoundError(LookupError):
    pass


class RefundService:
    def __init__(
        self,
        orders: OrderRepository,
        sessions: SessionRepository,
        events: EventRepository,
    ) -> None:
        self._orders = orders
        self._sessions = sessions
        self._events = events

    async def request_refund(
        self, order_id: str, *, requested_at: datetime | None = None
    ) -> RefundDecision:
        order = await self._orders.get(order_id)
        if order is None:
            raise NotFoundError(f"Pedido {order_id} no encontrado.")
        session = await self._sessions.get(order.session_id)
        if session is None:
            raise NotFoundError(f"Sesión {order.session_id} no encontrada.")
        event = await self._events.get(session.event_id)
        if event is None:
            raise NotFoundError(f"Evento {session.event_id} no encontrado.")

        decision = evaluate_refund(
            order=order,
            session=session,
            event=event,
            requested_at=requested_at or datetime.now(UTC),
        )
        if decision.approved:
            await self._orders.save(replace(order, status=OrderStatus.REFUNDED))
        return decision

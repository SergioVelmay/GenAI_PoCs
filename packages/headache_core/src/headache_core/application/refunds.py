"""Caso de uso: solicitar un reembolso. Orquesta puertos + reglas de dominio; no conoce la nube."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from headache_core.application.common import NotFoundError, new_id
from headache_core.domain import (
    OrderStatus,
    Refund,
    RefundDecision,
    RefundStatus,
    evaluate_refund,
)
from headache_core.ports import UnitOfWork


@dataclass(frozen=True, slots=True)
class RefundOutcome:
    refund: Refund
    decision: RefundDecision


class RefundService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def request_refund(
        self, order_id: str, *, reason: str = "", requested_at: datetime | None = None
    ) -> RefundOutcome:
        requested_at = requested_at or datetime.now(UTC)
        async with self._uow as uow:
            # Bloqueo pesimista del pedido: dos solicitudes simultáneas se serializan aquí.
            order = await uow.orders.get(order_id, for_update=True)
            if order is None:
                raise NotFoundError(f"Pedido {order_id} no encontrado.")
            session = await uow.sessions.get(order.session_id)
            if session is None:
                raise NotFoundError(f"Sesión {order.session_id} no encontrada.")
            event = await uow.events.get(session.event_id)
            if event is None:
                raise NotFoundError(f"Evento {session.event_id} no encontrado.")

            decision = evaluate_refund(
                order=order, session=session, event=event, requested_at=requested_at
            )
            refund = Refund(
                id=new_id("ref"),
                order_id=order.id,
                amount=decision.amount,
                reason=reason,
                status=RefundStatus.APPROVED if decision.approved else RefundStatus.REJECTED,
                requested_at=requested_at,
            )
            await uow.refunds.add(refund)
            if decision.approved:
                await uow.orders.update_status(order.id, OrderStatus.REFUNDED)
                await uow.sessions.release(session.id, order.quantity)
            await uow.commit()
        return RefundOutcome(refund=refund, decision=decision)

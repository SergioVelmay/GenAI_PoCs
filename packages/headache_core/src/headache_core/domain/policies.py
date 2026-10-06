"""Reglas de negocio puras. Son la "fuente de verdad" que el harness de agentes reutilizará."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from headache_core.domain.models import Event, Money, Order, OrderStatus, Session

MAX_TICKETS_PER_ORDER = 10


class PurchaseNotAllowedError(Exception):
    """Una regla de negocio impide la compra (no es un fallo técnico)."""


def check_purchase(*, session: Session, quantity: int, now: datetime) -> None:
    if not 1 <= quantity <= MAX_TICKETS_PER_ORDER:
        raise PurchaseNotAllowedError(
            f"La cantidad debe estar entre 1 y {MAX_TICKETS_PER_ORDER} entradas."
        )
    if session.starts_at <= now:
        raise PurchaseNotAllowedError("La sesión ya ha comenzado.")
    if session.available < quantity:
        raise PurchaseNotAllowedError(f"Solo quedan {session.available} plazas.")


@dataclass(frozen=True, slots=True)
class RefundDecision:
    approved: bool
    amount: Money
    reason: str


def evaluate_refund(
    *, order: Order, session: Session, event: Event, requested_at: datetime
) -> RefundDecision:
    if order.session_id != session.id or session.event_id != event.id:
        raise ValueError("Pedido, sesión y evento no están relacionados entre sí.")

    nothing = Money(Decimal("0"), order.total.currency)

    if order.status is not OrderStatus.PAID:
        return RefundDecision(False, nothing, f"El pedido está en estado '{order.status}'.")
    if not event.refundable:
        return RefundDecision(False, nothing, "La categoría del evento no admite reembolsos.")

    deadline = session.starts_at - timedelta(hours=event.refund_window_hours)
    if requested_at > deadline:
        return RefundDecision(
            False, nothing, f"Fuera de plazo: el límite era {deadline:%Y-%m-%d %H:%M} UTC."
        )
    return RefundDecision(True, order.total, "Solicitud dentro de plazo.")

"""Reglas de negocio puras. Son la "fuente de verdad" que el harness de agentes reutilizará."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

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


class RefundAction(StrEnum):
    REFUND = "refund"
    DENY = "deny"
    ESCALATE = "escalate"


@dataclass(frozen=True, slots=True)
class RuleViolation:
    code: str
    message: str


def check_refund_resolution(
    *,
    action: RefundAction,
    amount: Money | None,
    order: Order,
    session: Session,
    event: Event,
    decided_at: datetime,
) -> list[RuleViolation]:
    """Valida una resolución propuesta (por un LLM o una persona) contra la política.

    El reembolso puede ser parcial (p. ej. una de dos entradas), nunca mayor que lo pagado.
    """
    if action is RefundAction.ESCALATE:
        return []
    decision = evaluate_refund(order=order, session=session, event=event, requested_at=decided_at)
    if action is RefundAction.DENY:
        if decision.approved:
            return [
                RuleViolation(
                    "deny_eligible", "La política permite este reembolso: no se puede denegar."
                )
            ]
        return []
    violations: list[RuleViolation] = []
    if not decision.approved:
        violations.append(
            RuleViolation("refund_not_allowed", f"La política no lo permite: {decision.reason}")
        )
    if amount is None or amount.amount <= 0:
        violations.append(RuleViolation("invalid_amount", "Un reembolso necesita importe > 0."))
    elif amount.currency != order.total.currency:
        violations.append(
            RuleViolation("currency_mismatch", f"El pedido se pagó en {order.total.currency}.")
        )
    elif amount.amount > order.total.amount:
        violations.append(
            RuleViolation(
                "amount_exceeds_paid",
                f"El reembolso no puede superar lo pagado ({order.total.amount:.2f} "
                f"{order.total.currency}).",
            )
        )
    return violations

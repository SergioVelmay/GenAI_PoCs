from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from headache_core.domain import Event, Money, Order, OrderStatus, Session, evaluate_refund


def test_refund_approved_within_window(order: Order, session: Session, event: Event) -> None:
    requested_at = session.starts_at - timedelta(hours=72)

    decision = evaluate_refund(order=order, session=session, event=event, requested_at=requested_at)

    assert decision.approved
    assert decision.amount == order.total


def test_refund_rejected_when_too_late(order: Order, session: Session, event: Event) -> None:
    requested_at = session.starts_at - timedelta(hours=47)

    decision = evaluate_refund(order=order, session=session, event=event, requested_at=requested_at)

    assert not decision.approved
    assert decision.amount.amount == 0
    assert "Fuera de plazo" in decision.reason


def test_refund_rejected_for_non_refundable_event(
    order: Order, session: Session, event: Event
) -> None:
    event = replace(event, refundable=False)

    decision = evaluate_refund(
        order=order, session=session, event=event, requested_at=order.created_at
    )

    assert not decision.approved


def test_refund_rejected_when_order_not_paid(order: Order, session: Session, event: Event) -> None:
    order = replace(order, status=OrderStatus.REFUNDED)

    decision = evaluate_refund(
        order=order, session=session, event=event, requested_at=order.created_at
    )

    assert not decision.approved


def test_inconsistent_aggregates_raise(order: Order, session: Session, event: Event) -> None:
    with pytest.raises(ValueError):
        evaluate_refund(
            order=replace(order, session_id="otra"),
            session=session,
            event=event,
            requested_at=order.created_at,
        )


@pytest.mark.parametrize(("amount", "currency"), [(Decimal("-1"), "EUR"), (Decimal("1"), "eur")])
def test_money_validation(amount: Decimal, currency: str) -> None:
    with pytest.raises(ValueError):
        Money(amount, currency)

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from headache_core.domain import (
    Event,
    Money,
    Order,
    OrderStatus,
    RefundAction,
    Session,
    check_refund_resolution,
    evaluate_refund,
)


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


def _codes(
    order: Order,
    session: Session,
    event: Event,
    action: RefundAction,
    amount: Money | None,
    hours_before: int = 72,
) -> list[str]:
    violations = check_refund_resolution(
        action=action,
        amount=amount,
        order=order,
        session=session,
        event=event,
        decided_at=session.starts_at - timedelta(hours=hours_before),
    )
    return [v.code for v in violations]


def test_refund_resolution_allows_full_and_partial_refunds(
    order: Order, session: Session, event: Event
) -> None:
    half = Money(order.total.amount / 2, order.total.currency)

    assert _codes(order, session, event, RefundAction.REFUND, order.total) == []
    assert _codes(order, session, event, RefundAction.REFUND, half) == []
    assert _codes(order, session, event, RefundAction.ESCALATE, None, hours_before=1) == []


def test_refund_resolution_enforces_policy(order: Order, session: Session, event: Event) -> None:
    total = order.total
    more = Money(total.amount + 1, total.currency)
    other = Money(total.amount, "USD" if total.currency != "USD" else "EUR")

    assert _codes(order, session, event, RefundAction.REFUND, more) == ["amount_exceeds_paid"]
    assert _codes(order, session, event, RefundAction.REFUND, other) == ["currency_mismatch"]
    assert _codes(order, session, event, RefundAction.REFUND, None) == ["invalid_amount"]
    assert _codes(order, session, event, RefundAction.DENY, None) == ["deny_eligible"]
    late = _codes(order, session, event, RefundAction.REFUND, total, hours_before=1)
    assert late == ["refund_not_allowed"]
    assert _codes(order, session, event, RefundAction.DENY, None, hours_before=1) == []

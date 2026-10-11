from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from harness import Violation, generate_validated
from harness.rules.refunds import RefundCase, RefundResolution, draft_refund_resolution
from headache_core.adapters.memory import FakeLLM
from headache_core.domain import Event, EventCategory, Money, Order, OrderStatus, Session
from headache_core.ports import ChatMessage, Role

PRICE = Money(Decimal("30.00"), "EUR")
ORDER = Order(
    "ord_1", "cus_1", "ses_1", 2, PRICE * 2, OrderStatus.PAID, datetime(2026, 9, 1, tzinfo=UTC)
)
SESSION = Session("ses_1", "evt_1", datetime(2026, 11, 20, 20, tzinfo=UTC), 100, 3)
EVENT = Event(
    "evt_1",
    "Candlelit Strings",
    EventCategory.CANDLELIT,
    "Madrid",
    "ven_1",
    "Velas.",
    PRICE,
    refundable=True,
    refund_window_hours=48,
)
IN_TIME = RefundCase(ORDER, SESSION, EVENT, datetime(2026, 10, 1, tzinfo=UTC), "No puedo ir.")
LATE = RefundCase(ORDER, SESSION, EVENT, datetime(2026, 11, 19, tzinfo=UTC), "No puedo ir.")


def _resolution(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "order_id": "ord_1",
        "action": "refund",
        "amount": "60.00",
        "currency": "EUR",
        "rationale": "Dentro de plazo.",
        "customer_reply": "Hemos tramitado tu solicitud.",
    }
    return json.dumps(base | overrides)


async def test_feedback_lets_the_model_fix_an_amount_above_what_was_paid() -> None:
    llm = FakeLLM([_resolution(amount="90.00"), _resolution(amount="30.00")])

    result = await draft_refund_resolution(llm, IN_TIME)

    assert result.value is not None and result.value.amount == "30.00"
    assert [len(a.violations) for a in result.attempts] == [1, 0]
    assert result.attempts[0].violations[0].code == "amount_exceeds_paid"
    feedback = llm.calls[1][-1]
    assert feedback.role is Role.USER
    assert "intento 1 de 3" in feedback.content and "[amount_exceeds_paid]" in feedback.content
    assert llm.calls[1][-2] == ChatMessage(Role.ASSISTANT, _resolution(amount="90.00"))
    assert llm.schemas[0] is not None and llm.schemas[0].name == "RefundResolution"
    assert len(result.responses) == 2


async def test_out_of_window_refund_is_escalated_after_max_attempts() -> None:
    llm = FakeLLM([_resolution()] * 3)

    result = await draft_refund_resolution(llm, LATE)

    assert result.escalated
    assert len(result.attempts) == 3 == len(llm.calls)
    assert [v.code for v in result.violations] == ["refund_not_allowed"]
    assert "Fuera de plazo" in result.violations[0].message


async def test_invalid_json_and_schema_errors_are_corrected() -> None:
    deny = _resolution(
        action="deny", amount=None, currency=None, customer_reply="Está fuera de plazo."
    )
    llm = FakeLLM(["no es json", _resolution(amount="60"), f"```json\n{deny}\n```"])

    result = await draft_refund_resolution(llm, LATE)

    assert result.value is not None and result.value.action == "deny"
    assert [a.violations[0].code for a in result.attempts[:2]] == ["invalid_json", "schema"]
    assert result.attempts[1].violations[0].path == "amount"


async def test_business_rules_catch_inconsistent_resolutions() -> None:
    llm = FakeLLM(
        [
            _resolution(order_id="ord_2"),
            _resolution(action="deny", amount=None, currency=None),
            _resolution(
                action="escalate",
                amount=None,
                currency=None,
                customer_reply="Te devolveremos el dinero. Dudas: soporte@example.com",
            ),
        ]
    )

    result = await draft_refund_resolution(llm, IN_TIME)

    assert result.escalated
    codes = [[v.code for v in a.violations] for a in result.attempts]
    assert codes == [
        ["order_mismatch"],
        ["deny_eligible"],
        ["reply_contradicts_action", "email"],
    ]


async def test_non_correctable_violation_escalates_without_retrying() -> None:
    def malicious(value: RefundResolution, _: RefundCase) -> list[Violation]:
        return [Violation("prompt_injection", "Contenido malicioso.", correctable=False)]

    llm = FakeLLM([_resolution()] * 3)

    result = await generate_validated(
        llm,
        [ChatMessage(Role.USER, "x")],
        RefundResolution,
        validators=[malicious],
        context=IN_TIME,
    )

    assert result.escalated and len(llm.calls) == 1

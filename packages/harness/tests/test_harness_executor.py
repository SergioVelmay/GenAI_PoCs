from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from harness import POLICY_DENIED, GuardedExecutor, turn_trace
from harness.rules.refunds import refund_tool_guards
from headache_core.adapters.memory import InMemoryUnitOfWork
from headache_core.adapters.ticketing_local import UnitOfWorkTicketing
from headache_core.application.tools import ToolContext, build_ticketing_tools
from headache_core.domain import Customer, Event, EventCategory, Money, Order, OrderStatus, Session
from headache_core.ports import ToolCall

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PRICE = Money(Decimal("30.00"), "EUR")
ANA = ToolContext(customer_id="cus_1")


def _executor() -> tuple[GuardedExecutor, InMemoryUnitOfWork]:
    uow = InMemoryUnitOfWork(
        customers=[Customer("cus_1", "Ana Pérez", "ana@example.com", "ES", "es")],
        events=[
            Event(
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
        ],
        sessions=[Session("ses_1", "evt_1", datetime(2026, 11, 20, 20, tzinfo=UTC), 100, 3)],
        orders=[
            Order("ord_1", "cus_1", "ses_1", 2, PRICE * 2, OrderStatus.PAID, NOW),
            Order("ord_2", "cus_1", "ses_1", 1, PRICE, OrderStatus.PAID, NOW),
        ],
    )
    tools = build_ticketing_tools(UnitOfWorkTicketing(lambda: uow, clock=lambda: NOW))
    return GuardedExecutor(tools, refund_tool_guards()), uow


def _refund(call_id: str, order_id: str, reason: str = "No puedo ir.") -> ToolCall:
    return ToolCall(call_id, "request_refund", json.dumps({"order_id": order_id, "reason": reason}))


async def test_contact_data_in_arguments_is_denied_before_reaching_the_backend() -> None:
    executor, uow = _executor()

    execution = await executor.execute(_refund("c1", "ord_1", "Escribidme a ana@example.com"), ANA)

    assert execution.error == POLICY_DENIED
    assert "[email]" in execution.output and "ana@example.com" not in execution.output
    assert uow.refunds.items == []


async def test_only_one_refund_per_turn() -> None:
    executor, _ = _executor()

    with turn_trace() as trace:
        first = await executor.execute(_refund("c1", "ord_1"), ANA)
        second = await executor.execute(_refund("c2", "ord_2"), ANA)

    assert first.error is None and json.loads(first.output)["approved"] is True
    assert second.error == POLICY_DENIED
    assert [e.call.id for e in trace.executions] == ["c1", "c2"]


async def test_without_a_turn_the_per_turn_limit_does_not_apply() -> None:
    executor, _ = _executor()

    first = await executor.execute(_refund("c1", "ord_1"), ANA)
    second = await executor.execute(_refund("c2", "ord_2"), ANA)

    assert first.error is None and second.error is None


async def test_schema_errors_skip_the_guards_and_reach_the_base_executor() -> None:
    executor, _ = _executor()

    execution = await executor.execute(ToolCall("c1", "request_refund", '{"order_id": 1}'), ANA)
    unknown = await executor.execute(ToolCall("c2", "drop_tables", "{}"), ANA)

    assert execution.error == "invalid_arguments"
    assert unknown.error == "unknown_tool"

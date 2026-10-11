"""Harness de producción del Knowledge & Booking Agent: el mismo para los siete sabores."""

from __future__ import annotations

from booking_agent.runtime import AgentRuntime, JournaledExecutor
from booking_agent.spec import AgentSpec
from harness import GuardedExecutor, GuardedRuntime
from harness.rules.refunds import refund_claims_grounded, refund_tool_guards


def guarded_executor(spec: AgentSpec) -> JournaledExecutor:
    return JournaledExecutor(spec.tools, inner=GuardedExecutor(spec.tools, refund_tool_guards()))


def guarded_agent(agent: AgentRuntime) -> GuardedRuntime:
    return GuardedRuntime(agent, output_guards=[refund_claims_grounded])

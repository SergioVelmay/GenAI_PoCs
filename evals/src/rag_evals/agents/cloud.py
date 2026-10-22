"""Modelos con coste para las suites de agentes y ataques: un solo `CostMeter` para el agente y
el juez, así el presupuesto acota los dos (fase 12.2)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from headache_core.application.agent_judge import AgentJudge, LLMAgentJudge
from headache_core.ports import LLMPort
from rag_evals.metering import CostMeter

JUDGES = ("native",)


@dataclass(frozen=True, slots=True)
class CloudModels:
    meter: CostMeter
    agent: LLMPort | None
    judge: AgentJudge | None


def build_cloud_models(budget: Decimal, *, agent: bool, judge: str | None) -> CloudModels:
    from headache_core.adapters.llm_factory import build_gateway, build_judge_llm, build_tracer
    from headache_core.config import Settings

    settings = Settings()
    meter = CostMeter(budget_usd=budget, forward_to=build_tracer(settings))
    return CloudModels(
        meter=meter,
        agent=build_gateway(settings, tracer=meter) if agent else None,
        # Fijo y fuera del router: nunca el mismo modelo que genera.
        judge=LLMAgentJudge(build_judge_llm(settings, tracer=meter)) if judge else None,
    )

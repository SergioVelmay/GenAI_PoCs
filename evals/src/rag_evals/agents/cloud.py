"""Modelos con coste para las suites de agentes y ataques: un solo `CostMeter` para el agente y
el juez, así el presupuesto acota los dos (fase 12.2)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from headache_core.application.agent_judge import AgentJudge, LLMAgentJudge
from headache_core.ports import LLMPort
from rag_evals.metering import CostMeter

if TYPE_CHECKING:
    from headache_core.config import Settings

JUDGES = ("native",)


@dataclass(frozen=True, slots=True)
class CloudModels:
    meter: CostMeter
    agent: LLMPort | None
    judge: AgentJudge | None


def with_model(settings: Settings, model: str | None) -> Settings:
    """Fuerza un despliegue de chat (A/B de modelos, fase 12.7): los dos niveles del router
    apuntan a él, así el router no puede enmascarar la diferencia."""
    if model is None:
        return settings
    azure = settings.azure_openai.model_copy(
        update={"chat_deployment": model, "cheap_chat_deployment": model}
    )
    return settings.model_copy(update={"azure_openai": azure})


def build_cloud_models(
    budget: Decimal,
    *,
    agent: bool,
    judge: str | None,
    model: str | None = None,
    meter: CostMeter | None = None,
) -> CloudModels:
    """`meter` compartido: en un experimento, el presupuesto cubre todas las variantes."""
    from headache_core.adapters.llm_factory import build_gateway, build_judge_llm, build_tracer
    from headache_core.config import Settings

    settings = with_model(Settings(), model)
    meter = meter or CostMeter(budget_usd=budget, forward_to=build_tracer(settings))
    return CloudModels(
        meter=meter,
        agent=build_gateway(settings, tracer=meter) if agent else None,
        # Fijo y fuera del router: nunca el mismo modelo que genera.
        judge=LLMAgentJudge(build_judge_llm(settings, tracer=meter)) if judge else None,
    )

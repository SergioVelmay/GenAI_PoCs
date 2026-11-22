"""Modelos con coste para las suites de agentes y ataques: un solo `CostMeter` para el agente y
el juez, así el presupuesto acota los dos (fase 12.2)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from genaipocs_core.application.agent_judge import AgentJudge, LLMAgentJudge
from genaipocs_core.ports import LLMPort
from rag_evals.metering import CostMeter
from rag_evals.retrieval_eval import Cloud, llm_cloud

if TYPE_CHECKING:
    from genaipocs_core.config import Settings

JUDGES = ("native",)


@dataclass(frozen=True, slots=True)
class CloudModels:
    meter: CostMeter
    agent: LLMPort | None
    judge: AgentJudge | None
    # Nube del proveedor (`LLM_PROVIDER`): carpeta de los informes con coste, así una ejecución
    # con Gemini no sobrescribe la de Azure (fase 8.G4).
    cloud: Cloud


def with_model(settings: Settings, model: str | None) -> Settings:
    """Fuerza un modelo de chat (A/B de modelos, fase 12.7): los dos niveles del router
    apuntan a él, así el router no puede enmascarar la diferencia.

    Se fuerza en la nube de `LLM_PROVIDER`, que es la que genera. Hasta la 12.G solo se forzaba
    el despliegue de Azure: con Gemini o Bedrock, las dos variantes usaban el mismo modelo y el
    A/B salía «sin diferencia» sin avisar. Los respaldos de otras nubes no se fuerzan: si el
    gateway cae a uno, esa llamada no es de la variante (se ve en el coste por modelo)."""
    if model is None:
        return settings
    from genaipocs_core.config import LLMProvider

    match settings.llm_provider:
        case LLMProvider.AZURE:
            section = "azure_openai"
            fields = ("chat_deployment", "cheap_chat_deployment")
        case LLMProvider.AWS:
            section = "bedrock"
            fields = ("chat_model_id", "cheap_chat_model_id")
        case LLMProvider.GCP:
            section = "gemini"
            fields = ("chat_model", "cheap_chat_model")
        case _:
            raise ValueError("El A/B de modelos necesita un proveedor real (LLM_PROVIDER)")
    current = getattr(settings, section)
    forced = current.model_copy(update=dict.fromkeys(fields, model))
    return settings.model_copy(update={section: forced})


def build_cloud_models(
    budget: Decimal,
    *,
    agent: bool,
    judge: str | None,
    model: str | None = None,
    meter: CostMeter | None = None,
) -> CloudModels:
    """`meter` compartido: en un experimento, el presupuesto cubre todas las variantes."""
    from genaipocs_core.adapters.llm_factory import build_gateway, build_judge_llm, build_tracer
    from genaipocs_core.config import Settings

    settings = with_model(Settings(), model)
    meter = meter or CostMeter(budget_usd=budget, forward_to=build_tracer(settings))
    return CloudModels(
        meter=meter,
        agent=build_gateway(settings, tracer=meter) if agent else None,
        # Fijo y fuera del router: nunca el mismo modelo que genera.
        judge=LLMAgentJudge(build_judge_llm(settings, tracer=meter)) if judge else None,
        cloud=llm_cloud(settings.llm_provider),
    )

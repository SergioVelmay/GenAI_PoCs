"""Composición de la evaluación online (fases 11.6 y 12.3): juez fijo y destinos de los scores."""

from __future__ import annotations

from headache_core.application.agent_judge import LLMAgentJudge
from headache_core.application.agent_online_eval import AgentOnlineEvaluator
from headache_core.application.online_eval import ScoreSink
from headache_core.config import Settings


def build_score_sinks(settings: Settings) -> list[ScoreSink]:
    """Langfuse si hay claves; si no, solo métricas (las puntuaciones no se pierden: van a
    Prometheus, y la traza del juez a Jaeger)."""
    if not (settings.langfuse.public_key and settings.langfuse.secret_key.get_secret_value()):
        return []
    from headache_core.adapters.langfuse_scores import LangfuseScoreSink

    return [LangfuseScoreSink.from_settings(settings.langfuse)]


def build_agent_evaluator(
    settings: Settings, *, sample_rate: float, max_per_minute: int
) -> AgentOnlineEvaluator | None:
    """Apagado (None) con `sample_rate = 0`: cada muestra es una llamada al juez (coste)."""
    if sample_rate == 0:
        return None
    from headache_core.adapters.llm_factory import build_judge_llm

    return AgentOnlineEvaluator(
        LLMAgentJudge(build_judge_llm(settings)),
        sample_rate=sample_rate,
        max_per_minute=max_per_minute,
        sinks=build_score_sinks(settings),
    )

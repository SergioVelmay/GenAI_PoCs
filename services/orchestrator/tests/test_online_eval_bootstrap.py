"""Fase 12.3: el juez online de los especialistas vive lo que el proceso (apagado por defecto)."""

from __future__ import annotations

import asyncio

import pytest

from headache_core.application.agent_judge import AgentJudgeSample
from headache_core.application.agent_online_eval import AgentEvalSample, AgentOnlineEvaluator
from headache_core.config import Settings
from headache_core.domain.agent_eval import AgentJudgement, Resolution
from orchestrator import bootstrap
from orchestrator.settings import OrchestratorSettings


class _Judge:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def judge(self, sample: AgentJudgeSample) -> AgentJudgement:
        self.calls += 1
        return AgentJudgement(None, 0, 0, Resolution.RESOLVED, True)


async def test_off_by_default() -> None:
    assert OrchestratorSettings().online_eval_rate == 0
    async with bootstrap.running_agent_evaluator(Settings(), OrchestratorSettings()) as evaluator:
        assert evaluator is None


async def test_the_worker_judges_in_the_background_and_stops_with_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    judge = _Judge()
    built: list[tuple[float, int]] = []

    def build(
        settings: Settings, *, sample_rate: float, max_per_minute: int
    ) -> AgentOnlineEvaluator:
        built.append((sample_rate, max_per_minute))
        return AgentOnlineEvaluator(judge, sample_rate=sample_rate, max_per_minute=max_per_minute)

    monkeypatch.setattr(bootstrap, "build_agent_evaluator", build)
    config = OrchestratorSettings(online_eval_rate=1.0, online_eval_per_minute=5)

    async with bootstrap.running_agent_evaluator(Settings(), config) as evaluator:
        assert evaluator is not None
        sample = AgentEvalSample("refunds", AgentJudgeSample(("hola",), "respuesta", ()))
        assert evaluator.offer(sample, stop_reason="answer")
        for _ in range(50):
            if judge.calls:
                break
            await asyncio.sleep(0.01)

    assert built == [(1.0, 5)]
    assert judge.calls == 1

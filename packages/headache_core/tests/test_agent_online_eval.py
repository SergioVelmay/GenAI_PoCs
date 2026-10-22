"""Fase 12.3: el juez de agentes sobre una muestra de turnos reales, fuera de la petición."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from headache_core.adapters.otel import recording_telemetry
from headache_core.application.agent_judge import AgentJudgeSample
from headache_core.application.agent_online_eval import (
    EVALUATOR,
    AgentEvalSample,
    AgentOnlineEvaluator,
    EvaluatedAgent,
)
from headache_core.application.agents.runtime import AgentTurn, StopReason
from headache_core.application.agents.tracing import TracedAgent
from headache_core.application.online_eval import Score
from headache_core.application.telemetry import genai, metrics
from headache_core.application.tools import ToolContext, ToolExecution
from headache_core.domain.agent_eval import AgentJudgement, Resolution
from headache_core.ports import ToolCall
from headache_core.ports.telemetry import TraceCarrier

CONTEXT = ToolContext(customer_id="cus_1")
ACCEPTABLE = AgentJudgement(1.0, 2, 0, Resolution.ESCALATED, True)


class RecordingJudge:
    name = "recording"

    def __init__(self, verdict: AgentJudgement = ACCEPTABLE, error: Exception | None = None):
        self.verdict = verdict
        self.error = error
        self.samples: list[AgentJudgeSample] = []

    async def judge(self, sample: AgentJudgeSample) -> AgentJudgement:
        self.samples.append(sample)
        if self.error:
            raise self.error
        return self.verdict


class Sink:
    def __init__(self) -> None:
        self.scores: list[tuple[str, float]] = []

    async def record(self, trace: TraceCarrier, score: Score) -> None:
        self.scores.append((score.name, score.value))


class ToolAgent:
    """Agente falso: cada turno ejecuta una tool (la apunta en el diario) y responde."""

    flavor = "fake"

    def __init__(self, stop_reason: StopReason = "answer") -> None:
        self.journal: list[ToolExecution] = [
            ToolExecution(ToolCall("c0", "list_my_orders", "{}"), "[]", None, 1.0)
        ]
        self.stop_reason: StopReason = stop_reason

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        call = ToolCall("c1", "request_refund", '{"order_id": "ord_1"}')
        self.journal.append(ToolExecution(call, "Requiere aprobación", "approval_required", 1.0))
        return AgentTurn("Tu solicitud queda pendiente de revisión.", self.stop_reason)


async def _redact(text: str) -> str:
    return text.replace("4111 1111 1111 1111", "<CREDIT_CARD>")


def _wrap(agent: ToolAgent, evaluator: AgentOnlineEvaluator, name: str = "refunds") -> TracedAgent:
    evaluated = EvaluatedAgent(
        agent, name=name, evaluator=evaluator, journal=agent.journal, redact=_redact
    )
    return TracedAgent(evaluated, name=name)


async def test_a_turn_is_judged_later_with_its_own_evidence_in_its_trace() -> None:
    judge, sink = RecordingJudge(), Sink()
    evaluator = AgentOnlineEvaluator(judge, sample_rate=1.0, sinks=[sink])
    agent = ToolAgent()

    with recording_telemetry() as recorded:
        turn = await _wrap(agent, evaluator).respond(
            "t1", "Mi tarjeta 4111 1111 1111 1111", CONTEXT
        )
        # El turno ya ha respondido: el juez aún no ha trabajado.
        assert turn.stop_reason == "answer" and judge.samples == []
        assert await evaluator.drain() == 1

    [sample] = judge.samples
    assert sample.user_messages == ("Mi tarjeta <CREDIT_CARD>",)
    # Solo la evidencia del turno, no lo que había antes en el diario.
    assert [(e.tool, e.error) for e in sample.evidence] == [("request_refund", "approval_required")]
    spans = recorded.spans()
    judged, agent_span = spans[f"evaluate {EVALUATOR}"], spans["invoke_agent refunds"]
    assert judged.parent is not None and judged.parent.span_id == agent_span.context.span_id
    assert dict(judged.attributes or {})["headache.eval.agent_acceptable"] == 1.0
    assert sink.scores == [
        ("agent_acceptable", 1.0),
        ("agent_misstated_actions", 0.0),
        ("agent_same_language", 1.0),
        ("agent_grounded", 1.0),
    ]
    verdicts = {
        genai.AGENT_NAME: "refunds",
        "headache.eval.acceptable": "true",
        "headache.eval.resolution": "escalado",
    }
    assert recorded.total(metrics.EVAL_AGENT_VERDICTS, **verdicts) == 1


@pytest.mark.parametrize("stop_reason", ["guardrail", "max_iterations"])
async def test_fixed_texts_are_not_judged(stop_reason: StopReason) -> None:
    evaluator = AgentOnlineEvaluator(RecordingJudge(), sample_rate=1.0)

    await _wrap(ToolAgent(stop_reason), evaluator).respond("t1", "hola", CONTEXT)

    assert evaluator.pending == 0


async def test_sampling_caps_the_cost() -> None:
    draws = iter([0.05, 0.5, 0.5])
    evaluator = AgentOnlineEvaluator(RecordingJudge(), sample_rate=0.1, rng=lambda: next(draws))
    agent = _wrap(ToolAgent(), evaluator)

    for _ in range(3):
        await agent.respond("t1", "hola", CONTEXT)

    assert evaluator.pending == 1


async def test_a_failing_judge_never_breaks_the_agent() -> None:
    judge = RecordingJudge(error=TimeoutError("juez caído"))
    evaluator = AgentOnlineEvaluator(judge, sample_rate=1.0)

    with recording_telemetry() as recorded:
        turn = await _wrap(ToolAgent(), evaluator).respond("t1", "hola", CONTEXT)
        await evaluator.drain()

    assert turn.answer.startswith("Tu solicitud")
    assert recorded.total(metrics.EVAL_SAMPLES, **{"headache.eval.outcome": "failed"}) == 1
    assert recorded.total(metrics.EVAL_AGENT_VERDICTS) == 0


async def test_an_unacceptable_answer_is_counted_per_agent() -> None:
    invented = AgentJudgement(0.5, 2, 1, Resolution.RESOLVED, True)
    evaluator = AgentOnlineEvaluator(RecordingJudge(invented), sample_rate=1.0)
    samples: Sequence[AgentEvalSample] = [
        AgentEvalSample("knowledge", AgentJudgeSample(("hola",), "respuesta", ()))
    ]

    with recording_telemetry() as recorded:
        assert evaluator.offer(samples[0], stop_reason="answer")
        assert not evaluator.offer(
            AgentEvalSample("knowledge", AgentJudgeSample(("hola",), "  ", ())),
            stop_reason="answer",
        )
        await evaluator.drain()

    verdicts = {genai.AGENT_NAME: "knowledge", "headache.eval.acceptable": "false"}
    assert recorded.total(metrics.EVAL_AGENT_VERDICTS, **verdicts) == 1

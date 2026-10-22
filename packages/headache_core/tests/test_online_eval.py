"""Fase 11.6: evaluación online con LLM-juez (muestreo, presupuesto, métrica y traza)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

import pytest

from headache_core.adapters.langfuse_scores import trace_id_of
from headache_core.adapters.memory import FakeLLM
from headache_core.adapters.otel import recording_telemetry
from headache_core.application.online_eval import (
    EVALUATOR,
    EvalSample,
    LLMFaithfulnessJudge,
    OnlineEvaluator,
)
from headache_core.application.telemetry import genai, metrics
from headache_core.ports import SpanKind

SAMPLE = EvalSample("¿Hasta cuándo puedo pedir el reembolso?", "Hasta 48 h antes.", ("doc#0",))
OFFER = {"cache": "miss", "mode": "generative", "degraded": False, "abstained": False}


async def _sources(ids: Sequence[str]) -> list[str]:
    return [f"Fuente {source_id}: reembolso hasta 48 horas antes del evento." for source_id in ids]


class FixedJudge:
    def __init__(self, score: float | None = 0.5, error: Exception | None = None) -> None:
        self.score = score
        self.error = error
        self.calls = 0

    async def faithfulness(
        self, question: str, answer: str, sources: Sequence[str]
    ) -> float | None:
        self.calls += 1
        if self.error:
            raise self.error
        return self.score


class Sink:
    def __init__(self) -> None:
        self.scores: list[tuple[str, float]] = []

    async def record(self, sample: EvalSample, *, name: str, score: float) -> None:
        self.scores.append((name, score))


async def test_llm_judge_scores_the_fraction_of_supported_claims() -> None:
    verdict = {
        "claims": [
            {"claim": "El reembolso se pide hasta 48 h antes.", "supported": True},
            {"claim": "Se devuelve el 100 %.", "supported": False},
        ]
    }
    llm = FakeLLM([json.dumps(verdict)])

    score = await LLMFaithfulnessJudge(llm).faithfulness("p", "r", ["fuente"])

    assert score == 0.5
    # Pregunta, respuesta y fuentes van como datos, no como instrucciones.
    assert json.loads(llm.calls[0][-1].content)["sources"] == ["fuente"]


@pytest.mark.parametrize(
    "override",
    [
        {"cache": "hit"},
        {"cache": "coalesced"},
        {"mode": "extractive"},
        {"degraded": True},
        {"abstained": True},
    ],
)
def test_only_freshly_generated_answers_are_eligible(override: dict[str, object]) -> None:
    evaluator = OnlineEvaluator(FixedJudge(), _sources, sample_rate=1.0)

    assert not evaluator.offer(SAMPLE, **{**OFFER, **override})  # type: ignore[arg-type]
    assert evaluator.pending == 0


async def test_sampled_answer_is_scored_in_the_request_trace() -> None:
    sink = Sink()
    evaluator = OnlineEvaluator(FixedJudge(0.6), _sources, sample_rate=1.0, sinks=[sink])

    with recording_telemetry() as recorded:
        with recorded.telemetry.span("POST /ask", kind=SpanKind.SERVER):
            sample = EvalSample(
                SAMPLE.question,
                SAMPLE.answer,
                SAMPLE.citations,
                trace=recorded.telemetry.inject(),
                attributes={genai.HEADACHE_PLAN: "public"},
            )
            assert evaluator.offer(sample, **OFFER)  # type: ignore[arg-type]
        # El juez trabaja después de responder, fuera de la petición.
        assert await evaluator.drain() == 1

    spans = recorded.spans()
    judge, request = spans[f"evaluate {EVALUATOR}"], spans["POST /ask"]
    assert judge.parent is not None and judge.parent.span_id == request.context.span_id
    assert dict(judge.attributes or {})[genai.LANGFUSE_OBSERVATION_TYPE] == "evaluator"
    assert sink.scores == [(EVALUATOR, 0.6)]
    [(attributes, point)] = recorded.points(metrics.EVAL_FAITHFULNESS)
    assert attributes[genai.HEADACHE_PLAN] == "public"
    assert (point.count, point.sum) == (1, pytest.approx(0.6))
    assert recorded.total(metrics.EVAL_SAMPLES, **{"headache.eval.outcome": "scored"}) == 1


async def test_sampling_rate_and_per_minute_budget_cap_the_cost() -> None:
    judge = FixedJudge()
    draws = iter([0.05, 0.5, 0.05, 0.05, 0.05, 0.05])
    clock = [0.0]
    evaluator = OnlineEvaluator(
        judge,
        _sources,
        sample_rate=0.1,
        max_per_minute=2,
        rng=lambda: next(draws),
        clock=lambda: clock[0],
    )

    with recording_telemetry() as recorded:
        offered = [evaluator.offer(SAMPLE, **OFFER) for _ in range(5)]  # type: ignore[arg-type]
        await evaluator.drain()
        clock[0] = 61.0  # Pasa el minuto: vuelve a haber presupuesto.
        evaluator.offer(SAMPLE, **OFFER)  # type: ignore[arg-type]
        await evaluator.drain()

    assert offered == [True, False, True, True, True]
    assert judge.calls == 3
    outcome = "headache.eval.outcome"
    assert recorded.total(metrics.EVAL_SAMPLES, **{outcome: "skipped"}) == 1
    assert recorded.total(metrics.EVAL_SAMPLES, **{outcome: "dropped"}) == 2
    assert recorded.total(metrics.EVAL_SAMPLES, **{outcome: "scored"}) == 3


async def test_a_full_queue_drops_instead_of_blocking_the_request() -> None:
    evaluator = OnlineEvaluator(FixedJudge(), _sources, sample_rate=1.0, queue_size=1)

    assert evaluator.offer(SAMPLE, **OFFER)  # type: ignore[arg-type]
    assert not evaluator.offer(SAMPLE, **OFFER)  # type: ignore[arg-type]


async def test_a_failing_judge_never_breaks_anything() -> None:
    evaluator = OnlineEvaluator(
        FixedJudge(error=TimeoutError("juez caído")), _sources, sample_rate=1.0
    )

    with recording_telemetry() as recorded:
        evaluator.offer(SAMPLE, **OFFER)  # type: ignore[arg-type]
        await evaluator.drain()

    assert recorded.total(metrics.EVAL_SAMPLES, **{"headache.eval.outcome": "failed"}) == 1
    assert recorded.points(metrics.EVAL_FAITHFULNESS) == []


async def test_worker_stops_cleanly() -> None:
    evaluator = OnlineEvaluator(FixedJudge(), _sources, sample_rate=1.0)
    stop = asyncio.Event()
    task = asyncio.create_task(evaluator.run(stop))
    evaluator.offer(SAMPLE, **OFFER)  # type: ignore[arg-type]
    await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, 1)

    assert evaluator.pending == 0


def test_langfuse_trace_id_from_traceparent() -> None:
    carrier = {"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}
    assert trace_id_of(carrier) == "0af7651916cd43dd8448eb211c80319c"
    assert trace_id_of({}) is None

"""Carril A de GCP (5.G2) con los tipos reales del SDK de Agent Platform, sin red ni coste."""

from importlib.util import find_spec
from pathlib import Path

import pytest
from agentplatform import types
from google.genai.types import Part

from genaipocs_core.config import GcpEvalSettings, Settings
from rag_evals.calibration import (
    ADJUDICATED_PATH,
    agreement,
    load_judged,
    load_labels,
    load_set,
    write_judged,
)
from rag_evals.gcp_eval import (
    JUDGE_NAME,
    RELEVANCE_METRIC,
    MetricOutcome,
    build_dataset,
    build_metrics,
    grounding_metric,
    judged_from_outcomes,
    outcomes_by_case,
)
from rag_evals.judges import JudgeScores

GROUNDING = grounding_metric("v2")
ITEMS = load_set()


def _result(scores: dict[int, dict[str, types.EvalCaseMetricResult]]) -> types.EvaluationResult:
    """Resultado como lo arma el SDK: un caso por muestra, una respuesta candidata."""
    return types.EvaluationResult(
        eval_case_results=[
            types.EvalCaseResult(
                eval_case_index=index,
                response_candidate_results=[
                    types.ResponseCandidateResult(response_index=0, metric_results=metrics)
                ],
            )
            # El orden de llegada no tiene por qué ser el del dataset.
            for index, metrics in reversed(scores.items())
        ]
    )


def _metric(name: str, score: float | None, error: str | None = None) -> types.EvalCaseMetricResult:
    return types.EvalCaseMetricResult(
        metric_name=name, score=score, explanation="x", error_message=error
    )


def test_dataset_carries_question_answer_and_joined_contexts() -> None:
    dataset = build_dataset(ITEMS)

    cases = dataset.eval_cases or []
    assert [case.eval_case_id for case in cases] == [item.id for item in ITEMS]
    first, item = cases[0], ITEMS[0]
    assert first.prompt is not None and first.prompt.parts is not None
    assert first.prompt.parts[0].text == item.question
    assert first.responses is not None and first.responses[0].response is not None
    assert first.responses[0].response.parts == [Part(text=item.answer)]
    assert first.context == "\n\n".join(item.contexts)


@pytest.mark.skipif(find_spec("pandas") is None, reason="extra evals (client.evals importa pandas)")
def test_the_sdk_sends_the_context_to_the_service() -> None:
    """`context` es un campo extra de `EvalCase`: sin él, GROUNDING daría 0 sin avisar."""
    from agentplatform._genai._evals_metric_handlers import _build_evaluation_instance

    case = (build_dataset(ITEMS[:1]).eval_cases or [])[0]
    assert case.responses is not None
    instance = _build_evaluation_instance(case, case.responses[0].response)

    assert instance.other_data is not None and instance.other_data.map_instance is not None
    context = instance.other_data.map_instance["context"]
    assert context.text == "\n\n".join(ITEMS[0].contexts)


def test_metrics_are_the_managed_grounding_and_our_versioned_relevance() -> None:
    settings = Settings(_env_file=None)

    grounding, relevance = build_metrics(settings)

    assert grounding.name == "grounding_v2"
    assert isinstance(relevance, types.LLMMetric)
    assert relevance.name == RELEVANCE_METRIC
    # El mismo juez que el nativo, para comparar 1:1 (como Foundry con gpt-4.1).
    assert relevance.judge_model == settings.gemini.judge_model == "gemini-4-argon"
    assert relevance.prompt_template is not None
    assert set(types.PromptTemplate(text=relevance.prompt_template).variables) == {
        "prompt",
        "response",
    }


def test_metric_version_and_judge_come_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        gcp_eval=GcpEvalSettings(grounding_version="v1", relevance_judge_model="gemini-x"),
    )

    grounding, relevance = build_metrics(settings)

    assert grounding.name == "grounding_v1"
    assert relevance.judge_model == "gemini-x"


def test_scores_are_normalized_in_dataset_order() -> None:
    result = _result(
        {
            0: {GROUNDING: _metric(GROUNDING, 1.0), RELEVANCE_METRIC: _metric("r", 3)},
            1: {GROUNDING: _metric(GROUNDING, 0.0), RELEVANCE_METRIC: _metric("r", 2)},
        }
    )

    judged = judged_from_outcomes(ITEMS[:2], outcomes_by_case(result, 2), GROUNDING)

    assert [item.id for item in judged] == [ITEMS[0].id, ITEMS[1].id]
    assert judged[0].scores is not None and judged[1].scores is not None
    assert (judged[0].scores.faithfulness, judged[0].scores.answer_relevancy) == (1.0, 1.0)
    assert (judged[1].scores.faithfulness, judged[1].scores.answer_relevancy) == (0.0, 0.6667)


@pytest.mark.parametrize(
    ("metrics", "error"),
    [
        (
            {GROUNDING: _metric(GROUNDING, None, "quota"), RELEVANCE_METRIC: _metric("r", 3)},
            "quota",
        ),
        ({GROUNDING: _metric(GROUNDING, 1.0), RELEVANCE_METRIC: _metric("r", 5)}, "fuera de 0-3"),
        ({GROUNDING: _metric(GROUNDING, 1.5), RELEVANCE_METRIC: _metric("r", 3)}, "fuera de 0-1"),
        ({RELEVANCE_METRIC: _metric("r", 3)}, "ausente"),
    ],
)
def test_service_errors_are_recorded_not_scored(
    metrics: dict[str, types.EvalCaseMetricResult], error: str
) -> None:
    """Fail-closed por muestra, como el juez nativo: no se puntúa a ciegas ni se aborta."""
    judged = judged_from_outcomes(ITEMS[:1], outcomes_by_case(_result({0: metrics}), 1), GROUNDING)

    assert judged[0].scores is None
    assert judged[0].error is not None and error in judged[0].error


def test_a_result_that_does_not_match_the_dataset_is_rejected() -> None:
    with pytest.raises(ValueError, match="fuera del dataset"):
        outcomes_by_case(_result({3: {}}), 2)
    with pytest.raises(ValueError, match="1 casos para 2"):
        judged_from_outcomes(ITEMS[:2], [{}], GROUNDING)


def test_report_feeds_calibration_agreement(tmp_path: Path) -> None:
    """Un evaluador que acierta todo detecta cada tipo estropeado, como el juez nativo."""
    faithful = {"good": 1.0, "fabricated": 0.0, "unsupported": 0.0, "off_topic": 0.0}
    outcomes = [
        {
            GROUNDING: MetricOutcome(faithful.get(item.kind, 0.0), None, None),
            RELEVANCE_METRIC: MetricOutcome(0 if item.kind == "off_topic" else 3, None, None),
        }
        for item in ITEMS
    ]
    judged = judged_from_outcomes(ITEMS, outcomes, GROUNDING)
    path = write_judged(JUDGE_NAME, judged, {"cost_usd": None}, tmp_path)

    name, loaded = load_judged(path)
    result = agreement(name, loaded, load_labels(ADJUDICATED_PATH))

    assert (name, result.judge_errors) == ("gcp_eval", 0)
    assert {kind: result.detected[kind] for kind in faithful} == {
        "good": "8/8",
        "fabricated": "4/4",
        "unsupported": "4/4",
        "off_topic": "2/2",
    }


@pytest.mark.skipif(find_spec("pandas") is None, reason="extra evals (client.evals importa pandas)")
def test_full_sdk_flow_with_only_the_http_call_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """`client.evals.evaluate` de verdad (dataset, handlers, agregación); solo se sustituye
    `_evaluate_instances`, la llamada HTTP. Fija lo que los dobles no ven: que el SDK acepta el
    dataset, que envía `context` a cada métrica y con qué nombre devuelve cada resultado."""
    import agentplatform
    from agentplatform._genai import evals
    from google.auth.credentials import AnonymousCredentials

    sent: list[tuple[str, bool]] = []

    def evaluate_instances(self: object, **kwargs: object) -> types.EvaluateInstancesResponse:
        metric = kwargs["metrics"][0]  # type: ignore[index]
        instance = kwargs["instance"]
        other = instance.other_data  # type: ignore[attr-defined]
        sent.append((metric.name, "context" in (other.map_instance or {})))
        score = 1.0 if metric.name == GROUNDING else 2.0
        return types.EvaluateInstancesResponse(
            metric_results=[types.MetricResult(score=score, explanation="ok")]
        )

    monkeypatch.setattr(evals.Evals, "_evaluate_instances", evaluate_instances)
    credentials = AnonymousCredentials()  # type: ignore[no-untyped-call]
    client = agentplatform.Client(project="p", location="europe-west4", credentials=credentials)
    items = ITEMS[:3]

    result = client.evals.evaluate(
        dataset=build_dataset(items), metrics=build_metrics(Settings(_env_file=None))
    )
    judged = judged_from_outcomes(items, outcomes_by_case(result, 3), GROUNDING)

    assert sorted(sent) == sorted([(GROUNDING, True), (RELEVANCE_METRIC, True)] * 3)
    assert [item.scores for item in judged] == [JudgeScores(1.0, 0.6667, None, None)] * 3

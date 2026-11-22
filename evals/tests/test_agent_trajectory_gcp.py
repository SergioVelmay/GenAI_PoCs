"""Fase 12.G2: trayectoria propia frente al Gen AI Evaluation Service (coste 0, doble del servicio).

El doble devuelve los tipos reales de `aiplatform_v1beta1` y puntúa cada instancia con la
definición documentada: así se prueba la petición, el orden y los lotes, no la definición.
"""

from __future__ import annotations

import pytest
from google.cloud.aiplatform_v1beta1 import types

from rag_evals.agents.gcp_eval import (
    DEFAULT_REPORT,
    METRICS,
    OUT_NAME,
    Metric,
    TrajectoryCase,
    build_request,
    contrast,
    documented_metrics,
    evaluate_service,
    load_cases,
    our_metrics,
    render,
    scores_from_response,
)
from rag_evals.retrieval_eval import REPORTS_DIR

LOCATION = "projects/genaipocs-dev/locations/europe-west4"
RESULT_TYPES = {
    "trajectory_exact_match": "TrajectoryExactMatch",
    "trajectory_in_order_match": "TrajectoryInOrderMatch",
    "trajectory_any_order_match": "TrajectoryAnyOrderMatch",
    "trajectory_precision": "TrajectoryPrecision",
    "trajectory_recall": "TrajectoryRecall",
}


class FakeEvaluationService:
    """Doble de `EvaluationServiceAsyncClient.evaluate_instances`."""

    def __init__(self) -> None:
        self.requests: list[types.EvaluateInstancesRequest] = []

    async def evaluate_instances(
        self, request: types.EvaluateInstancesRequest
    ) -> types.EvaluateInstancesResponse:
        self.requests.append(request)
        metric: Metric = next(m for m in METRICS if f"{m}_input" in request)
        scores = []
        for instance in getattr(request, f"{metric}_input").instances:
            actual = [call.tool_name for call in instance.predicted_trajectory.tool_calls]
            expected = [call.tool_name for call in instance.reference_trajectory.tool_calls]
            scores.append(documented_metrics(expected, actual)[metric])
        prefix = RESULT_TYPES[metric]
        value_type = getattr(types, f"{prefix}MetricValue")
        results_type = getattr(types, f"{prefix}Results")
        results = results_type(**{f"{metric}_metric_values": [value_type(score=s) for s in scores]})
        return types.EvaluateInstancesResponse(**{f"{metric}_results": results})


@pytest.mark.parametrize(
    ("expected", "actual", "documented", "ours"),
    [
        # Repetición: para Google una sola llamada cubre las dos esperadas; para la propia, no.
        (["remember", "remember", "search"], ["remember"], (1.0, 2 / 3), (1.0, 1 / 3)),
        # Llamadas de más con el mismo nombre: Google las da por buenas.
        (["get", "search"], ["load", "get", "event", "get", "search"], (3 / 5, 1.0),
         (2 / 5, 1.0)),
        # Desorden: Google no mira el orden en precisión ni recall.
        (["a", "b"], ["b", "a"], (1.0, 1.0), (0.5, 0.5)),
    ],
)  # fmt: skip
def test_documented_precision_and_recall_ignore_order_and_repetitions(
    expected: list[str],
    actual: list[str],
    documented: tuple[float, float],
    ours: tuple[float, float],
) -> None:
    google = documented_metrics(expected, actual)
    own = our_metrics(expected, actual)

    assert (google["trajectory_precision"], google["trajectory_recall"]) == pytest.approx(
        documented
    )
    assert (own["trajectory_precision"], own["trajectory_recall"]) == pytest.approx(ours)


def test_match_metrics_follow_the_documentation() -> None:
    google = documented_metrics(["a", "b"], ["a", "x", "b"])
    assert google["trajectory_exact_match"] == 0.0
    assert google["trajectory_in_order_match"] == 1.0
    assert google["trajectory_any_order_match"] == 1.0
    # Cualquier orden sigue exigiendo cada esperada tantas veces como aparece.
    assert documented_metrics(["a", "a"], ["a"])["trajectory_any_order_match"] == 0.0
    assert documented_metrics(["b", "a"], ["a", "b"])["trajectory_in_order_match"] == 0.0
    assert documented_metrics([], []) == dict.fromkeys(METRICS, 1.0)


def test_real_trajectories_of_the_azure_report() -> None:
    cases = load_cases(DEFAULT_REPORT)

    assert len(cases) == 84
    assert len({case.flavor for case in cases}) == 7
    assert all(case.expected or case.scenario_id.startswith("security") for case in cases)


def test_request_carries_one_metric_with_names_only() -> None:
    case = TrajectoryCase("native", "s1", ("load_skill", "get_order"), ("get_order",))

    request = build_request("trajectory_recall", [case], LOCATION)

    assert request.location == LOCATION
    assert "trajectory_recall_input" in request and "trajectory_precision_input" not in request
    instance = request.trajectory_recall_input.instances[0]
    assert [c.tool_name for c in instance.reference_trajectory.tool_calls] == [
        "load_skill",
        "get_order",
    ]
    assert [c.tool_input for c in instance.predicted_trajectory.tool_calls] == [""]


async def test_service_scores_keep_the_case_order_across_batches() -> None:
    cases = load_cases(DEFAULT_REPORT)
    service = FakeEvaluationService()

    scores = await evaluate_service(service, cases, LOCATION, batch=40)

    # 5 métricas por 3 lotes (40 + 40 + 4).
    assert len(service.requests) == 15
    result = contrast("informe", cases, scores)
    assert result.service_vs_documented == dict.fromkeys(METRICS, 0)
    assert result.mean_service == result.mean_documented
    assert "(ejecutado)" in render(result)


def test_a_short_response_is_an_error_not_a_gap() -> None:
    response = types.EvaluateInstancesResponse(
        trajectory_precision_results=types.TrajectoryPrecisionResults(
            trajectory_precision_metric_values=[types.TrajectoryPrecisionMetricValue(score=1.0)]
        )
    )

    with pytest.raises(ValueError, match="1 resultados para 2"):
        scores_from_response("trajectory_precision", response, 2)


def test_committed_report_is_reproducible() -> None:
    """El informe de coste 0 se regenera igual: si cambia la medida propia, cambia el informe."""
    source = DEFAULT_REPORT.relative_to(REPORTS_DIR.parent.parent).as_posix()
    committed = (REPORTS_DIR / "gcp" / f"{OUT_NAME}.md").read_text(encoding="utf-8")

    assert render(contrast(source, load_cases(DEFAULT_REPORT))) == committed

"""Carril A de las evals en GCP: Gen AI Evaluation Service sobre la calibracion (5.G2).

El equivalente de `foundry_eval`. Dos metricas sobre `calibration-v1` (20 muestras, 12 estropeadas
a proposito y etiquetadas por un humano):

- Fidelidad: `grounding_v2`, rubrica gestionada del servicio. Juzga la respuesta frase a frase
  frente al contexto y da 0 si alguna frase no esta respaldada o lo contradice; si no, la
  fraccion de frases respaldadas. Es el mismo criterio que "fiel" en la calibracion.
- Relevancia: metrica LLM propia (`genaipocs_relevance`, prompt versionado `gcp-eval-relevance`)
  con la escala 0-3 del juez nativo y el criterio de las etiquetas adjudicadas. El servicio no
  tiene una equivalente a `RelevanceEvaluator` de Foundry (decidido con Sergio).

El resultado se guarda con el formato de `rag_evals.calibration judge` en `evals/reports/gcp/`,
asi `calibration agreement --cloud gcp` lo compara con el juez nativo, RAGAS y DeepEval.

Cliente `client.evals` del SDK de Agent Platform (Preview; importa pandas, que llega con el extra
`evals`). Uso (con coste; gcloud aislado y extra `evals`):
    uv run --no-sync python -m rag_evals.gcp_eval [--dest gs://<bucket>/evals/]
"""

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from rag_evals.calibration import CalibrationItem, JudgedItem, load_set, write_judged
from rag_evals.judges import JudgeScores
from rag_evals.prompts import PROMPTS
from rag_evals.retrieval_eval import cloud_reports_dir

if TYPE_CHECKING:
    from agentplatform import types

    from genaipocs_core.config import Settings

JUDGE_NAME = "gcp_eval"
RELEVANCE_METRIC = "genaipocs_relevance"
RELEVANCE_PROMPT = "gcp-eval-relevance"
# Escala de la metrica propia: la del juez nativo (0 no responde ... 3 responde del todo).
RELEVANCE_MAX = 3


def grounding_metric(version: str) -> str:
    """Nombre de la metrica predefinida (`grounding_v1` o `grounding_v2`): es la clave del
    resultado."""
    return f"grounding_{version}"


@dataclass(frozen=True, slots=True)
class MetricOutcome:
    score: float | None
    explanation: str | None
    error: str | None


def build_dataset(items: Sequence[CalibrationItem]) -> "types.EvaluationDataset":
    """Un caso por muestra: pregunta, respuesta y contextos unidos (el `context` de GROUNDING)."""
    from agentplatform import types
    from google.genai import types as genai_types

    def content(text: str) -> genai_types.Content:
        return genai_types.Content(role="user", parts=[genai_types.Part(text=text)])

    return types.EvaluationDataset(
        eval_cases=[
            # `EvalCase` admite campos extra: `context` es el que leen las metricas de grounding.
            types.EvalCase(
                eval_case_id=item.id,
                prompt=content(item.question),
                responses=[types.ResponseCandidate(response=content(item.answer))],
                context="\n\n".join(item.contexts),
            )
            for item in items
        ]
    )


def build_metrics(settings: "Settings") -> list["types.Metric"]:
    """La rubrica gestionada de fidelidad y la metrica propia de relevancia."""
    from agentplatform import types

    evals = settings.gcp_eval
    return [
        types.Metric(name=grounding_metric(evals.grounding_version)),
        types.LLMMetric(
            name=RELEVANCE_METRIC,
            prompt_template=PROMPTS.get(RELEVANCE_PROMPT).text,
            judge_model=evals.relevance_judge_model or settings.gemini.judge_model,
        ),
    ]


def outcomes_by_case(
    result: "types.EvaluationResult", cases: int
) -> list[dict[str, MetricOutcome]]:
    """Resultados por caso y metrica, en el orden del dataset (por `eval_case_index`)."""
    outcomes: list[dict[str, MetricOutcome]] = [{} for _ in range(cases)]
    for case in result.eval_case_results or []:
        index = case.eval_case_index
        if index is None or not 0 <= index < cases:
            raise ValueError(f"Caso fuera del dataset: {index}")
        candidates = case.response_candidate_results or []
        metrics = (candidates[0].metric_results or {}) if candidates else {}
        outcomes[index] = {
            name: MetricOutcome(metric.score, metric.explanation, metric.error_message)
            for name, metric in metrics.items()
        }
    return outcomes


def _scaled(outcome: MetricOutcome | None, maximum: float) -> float | str:
    """Puntuacion en 0-1 o el motivo por el que no la hay (fail-closed, como el juez nativo)."""
    if outcome is None:
        return "metrica ausente en la respuesta del servicio"
    if outcome.error:
        return outcome.error
    if outcome.score is None or not 0 <= outcome.score <= maximum:
        return f"puntuacion ausente o fuera de 0-{maximum:g}: {outcome.score}"
    return round(outcome.score / maximum, 4)


def judged_from_outcomes(
    items: Sequence[CalibrationItem],
    outcomes: Sequence[Mapping[str, MetricOutcome]],
    grounding: str,
) -> list[JudgedItem]:
    if len(outcomes) != len(items):
        raise ValueError(f"El servicio devolvio {len(outcomes)} casos para {len(items)} muestras")
    judged: list[JudgedItem] = []
    for item, by_metric in zip(items, outcomes, strict=True):
        faithfulness = _scaled(by_metric.get(grounding), 1)
        relevance = _scaled(by_metric.get(RELEVANCE_METRIC), RELEVANCE_MAX)
        if isinstance(faithfulness, str) or isinstance(relevance, str):
            errors = [value for value in (faithfulness, relevance) if isinstance(value, str)]
            judged.append(JudgedItem(item.id, item.kind, None, "; ".join(errors)))
            continue
        judged.append(
            JudgedItem(item.id, item.kind, JudgeScores(faithfulness, relevance, None, None), None)
        )
    return judged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest", help="gs://... donde el servicio guarda tambien el resultado (opcional)"
    )
    args = parser.parse_args()

    import agentplatform
    from agentplatform import types

    from genaipocs_core.adapters.gcp_auth import gcp_credentials
    from genaipocs_core.config import Settings

    settings = Settings()
    gcp, evals = settings.gcp, settings.gcp_eval
    if not gcp.project:
        raise ValueError("Falta GCP__PROJECT")
    client = agentplatform.Client(
        project=gcp.project,
        location=evals.location or gcp.region,
        credentials=gcp_credentials(gcp),
    )
    items = load_set()
    metrics = build_metrics(settings)
    result = client.evals.evaluate(
        dataset=build_dataset(items),
        metrics=list(metrics),
        config=types.EvaluateMethodConfig(
            dest=args.dest, allow_cross_region_model=evals.allow_cross_region_model
        ),
    )
    grounding = grounding_metric(evals.grounding_version)
    judged = judged_from_outcomes(items, outcomes_by_case(result, len(items)), grounding)
    # El servicio no devuelve tokens: se factura con el SKU del modelo juez (ver la consola).
    cost = {
        "cost_usd": None,
        "metric_calls": len(items) * len(metrics),
        "metrics": [metric.name for metric in metrics],
        "relevance_judge": metrics[1].judge_model,
    }
    path = write_judged(JUDGE_NAME, judged, cost, cloud_reports_dir("gcp"))
    print(f"{path}: {sum(i.error is None for i in judged)} puntuadas de {len(judged)}")
    print(json.dumps(asdict(judged[0]), ensure_ascii=False, default=str)[:300])


if __name__ == "__main__":
    main()

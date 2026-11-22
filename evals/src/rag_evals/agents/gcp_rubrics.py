"""Rúbricas gestionadas del Gen AI Evaluation Service frente al juez de agentes (12.G2).

El contraste del `AgentJudge` (12.2) en el carril A de GCP, como `gcp_eval` lo es del juez de RAG
(5.G2). Puntúa la calibración de agentes (`agents-v1`, 32 muestras) con dos métricas
gestionadas de `client.evals` y las compara con las mismas etiquetas humanas:

- `hallucination_v2`: parte la respuesta en afirmaciones y comprueba cada una contra las
  llamadas y salidas de tools (`intermediate_events`). Puntuación 0-1: fracción respaldada. Se
  compara con `grounded` (respaldada = 1, como el juez nativo).
- `final_response_quality_v2`: genera rúbricas a partir de las instrucciones del agente y de la
  petición, y da la fracción que la respuesta cumple. Se compara con `acceptable` con un umbral
  (`--threshold`, 0,8 por defecto) que se calibra con las etiquetas.

`tool_use_quality_v2` no se usa aquí: en la calibración todas las variantes de una muestra
comparten las mismas tools (solo cambia la respuesta) y no hay etiqueta humana de uso de tools.

Coste (precios de la documentación, 22-11-2026): 2 llamadas a Gemini 3.5 Flash por muestra en
`hallucination` y 5 más 1 a Gemini 3.1 Pro en `final_response_quality`; unos céntimos para 32.
Uso (extra `evals`: `client.evals` importa pandas; gcloud aislado):
    uv run --no-sync python -m rag_evals.agents.gcp_rubrics judge        # con coste
    uv run python -m rag_evals.agents.gcp_rubrics agreement              # coste 0
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from genaipocs_core.domain.evals import cohen_kappa
from rag_evals.agent_calibration import (
    EXPECTED_ACCEPTABLE,
    AgentCalibrationItem,
    HumanLabel,
    load_labels,
    load_set,
)
from rag_evals.gcp_eval import MetricOutcome, outcomes_by_case
from rag_evals.retrieval_eval import cloud_reports_dir

if TYPE_CHECKING:
    from agentplatform import types

HALLUCINATION = "hallucination_v2"
FINAL_RESPONSE_QUALITY = "final_response_quality_v2"
METRICS = (HALLUCINATION, FINAL_RESPONSE_QUALITY)
DEFAULT_THRESHOLD = 0.8
REPORT_NAME = "agent-calibration-gcp_rubrics"

type YesNo = Literal["si", "no"]


def _text(role: str, text: str) -> object:
    from google.genai import types as genai_types

    return genai_types.Content(role=role, parts=[genai_types.Part(text=text)])


def _arguments(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def build_case(item: AgentCalibrationItem, instructions: str) -> types.EvalCase:
    """Una muestra: lo que vio el agente (mensajes y tools) y su respuesta.

    Cada evidencia son dos eventos: la llamada del modelo (`function_call`) y su resultado
    (`function_response`, con el error si lo hubo). Los mensajes anteriores del cliente van en
    el historial; el último es la petición.
    """
    from agentplatform import types
    from google.genai import types as genai_types

    events: list[types.evals.Event] = []
    for evidence in item.evidence:
        call = genai_types.FunctionCall(name=evidence.tool, args=_arguments(evidence.arguments))
        result: dict[str, object] = {"output": evidence.output}
        if evidence.error:
            result["error"] = evidence.error
        events += [
            types.evals.Event(
                author="model",
                content=genai_types.Content(
                    role="model", parts=[genai_types.Part(function_call=call)]
                ),
            ),
            types.evals.Event(
                author="user",
                content=genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part(
                            function_response=genai_types.FunctionResponse(
                                name=evidence.tool, response=result
                            )
                        )
                    ],
                ),
            ),
        ]
    *history, last = item.user_messages
    return types.EvalCase(
        eval_case_id=item.id,
        system_instruction=_text("system", instructions),
        conversation_history=[
            types.evals.Message(turn_id=str(index), author="user", content=_text("user", text))
            for index, text in enumerate(history)
        ]
        or None,
        prompt=_text("user", last),
        responses=[types.ResponseCandidate(response=_text("model", item.answer))],
        intermediate_events=events or None,
    )


@dataclass(frozen=True, slots=True)
class RubricScores:
    id: str
    kind: str
    hallucination: float | None
    final_response_quality: float | None
    error: str | None


def _score(outcome: MetricOutcome | None) -> float | str:
    if outcome is None:
        return "métrica ausente en la respuesta del servicio"
    if outcome.error:
        return outcome.error
    if outcome.score is None or not 0 <= outcome.score <= 1:
        return f"puntuación ausente o fuera de 0-1: {outcome.score}"
    return round(outcome.score, 4)


def scores_from_outcomes(
    items: Sequence[AgentCalibrationItem], outcomes: Sequence[Mapping[str, MetricOutcome]]
) -> list[RubricScores]:
    """Fail-closed por muestra, como el juez nativo: una métrica que falla deja la muestra sin
    puntuar (cuenta como error), nunca como aprobada."""
    if len(outcomes) != len(items):
        raise ValueError(f"El servicio devolvió {len(outcomes)} casos para {len(items)} muestras")
    scored: list[RubricScores] = []
    for item, by_metric in zip(items, outcomes, strict=True):
        hallucination = _score(by_metric.get(HALLUCINATION))
        quality = _score(by_metric.get(FINAL_RESPONSE_QUALITY))
        errors = [value for value in (hallucination, quality) if isinstance(value, str)]
        if errors:
            scored.append(RubricScores(item.id, item.kind, None, None, "; ".join(errors)))
        else:
            assert isinstance(hallucination, float) and isinstance(quality, float)
            scored.append(RubricScores(item.id, item.kind, hallucination, quality, None))
    return scored


def predicted_labels(scores: RubricScores, threshold: float) -> dict[str, YesNo] | None:
    """Las dos etiquetas que se pueden comparar con las humanas (None si la muestra falló)."""
    if scores.hallucination is None or scores.final_response_quality is None:
        return None
    return {
        "grounded": "si" if scores.hallucination >= 1.0 else "no",
        "acceptable": "si" if scores.final_response_quality >= threshold else "no",
    }


@dataclass(frozen=True, slots=True)
class RubricAgreement:
    threshold: float
    samples: int
    errors: int
    accuracy: dict[str, float | None]
    kappa: dict[str, float | None]
    # Muestras estropeadas que la rúbrica da por NO aceptables (no necesita etiquetas).
    detected: dict[str, str]


def agreement(
    scored: Sequence[RubricScores], labels: Mapping[str, HumanLabel], threshold: float
) -> RubricAgreement:
    predicted = {s.id: predicted_labels(s, threshold) for s in scored}
    accuracy: dict[str, float | None] = {}
    kappa: dict[str, float | None] = {}
    for dimension in ("grounded", "acceptable"):
        pairs = [
            (getattr(labels[item_id], dimension), labels_[dimension])
            for item_id, labels_ in predicted.items()
            if labels_ is not None
            and item_id in labels
            and getattr(labels[item_id], dimension) in ("si", "no")
        ]
        human = [p[0] for p in pairs]
        machine = [p[1] for p in pairs]
        accuracy[dimension] = (
            round(sum(h == m for h, m in pairs) / len(pairs), 3) if pairs else None
        )
        value = cohen_kappa(human, machine)
        kappa[dimension] = None if value is None else round(value, 3)
    detected: dict[str, str] = {}
    for kind, expected in EXPECTED_ACCEPTABLE.items():
        of_kind = [predicted[s.id] for s in scored if s.kind == kind and predicted[s.id]]
        hits = sum(1 for p in of_kind if p and p["acceptable"] == expected)
        detected[kind] = f"{hits}/{len(of_kind)}"
    return RubricAgreement(
        threshold=threshold,
        samples=sum(1 for s in scored if s.id in labels and s.error is None),
        errors=sum(s.error is not None for s in scored),
        accuracy=accuracy,
        kappa=kappa,
        detected=detected,
    )


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def render(results: Sequence[RubricAgreement], total: int) -> str:
    lines = [
        "# Rúbricas del Evaluation Service frente a etiquetas humanas (12.G2)",
        "",
        f"- Conjunto `agents-v1` ({total} muestras), las mismas etiquetas que el juez nativo "
        "(`agent-calibration.md`).",
        "- `grounded` ← `hallucination_v2` = 1; `acceptable` ← `final_response_quality_v2` ≥ "
        "umbral.",
        "- Detección: muestras estropeadas que la rúbrica da por NO aceptables (sin etiquetas).",
        "",
        "| Umbral | Muestras | Errores | grounded: acierto / kappa | acceptable: acierto / kappa | "
        + " | ".join(EXPECTED_ACCEPTABLE)
        + " |",
        "|---:|---:|---:|---:|---:|" + "---:|" * len(EXPECTED_ACCEPTABLE),
    ]
    for r in results:
        lines.append(
            f"| {r.threshold:g} | {r.samples} | {r.errors} | "
            f"{_fmt(r.accuracy['grounded'])} / {_fmt(r.kappa['grounded'])} | "
            f"{_fmt(r.accuracy['acceptable'])} / {_fmt(r.kappa['acceptable'])} | "
            + " | ".join(r.detected[k] for k in EXPECTED_ACCEPTABLE)
            + " |"
        )
    return "\n".join(lines) + "\n"


def write_scores(scored: Sequence[RubricScores], meta: Mapping[str, object], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{REPORT_NAME}.json"
    payload = {"meta": dict(meta), "items": [asdict(s) for s in scored]}
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return path


def load_scores(path: Path) -> list[RubricScores]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [RubricScores(**item) for item in payload["items"]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    judge = commands.add_parser("judge", help="Puntúa la calibración con el servicio (con coste)")
    judge.add_argument("--dest", help="gs://... donde el servicio guarda también el resultado")
    agree = commands.add_parser("agreement", help="Acuerdo con las etiquetas humanas (coste 0)")
    agree.add_argument(
        "--threshold", type=float, action="append", help="Umbral de acceptable (repetible)"
    )
    args = parser.parse_args()
    out_dir = cloud_reports_dir("gcp")
    items = load_set()

    if args.command == "judge":
        import agentplatform
        from agentplatform import types

        from booking_agent.spec import BOOKING_AGENT_PROMPT
        from genaipocs_core.adapters.gcp_auth import gcp_credentials
        from genaipocs_core.config import Settings

        settings = Settings()
        gcp, evals = settings.gcp, settings.gcp_eval
        if not gcp.project:
            raise ValueError("Falta GCP__PROJECT")
        client = agentplatform.Client(
            project=gcp.project, location=evals.location or gcp.region,
            credentials=gcp_credentials(gcp),
        )  # fmt: skip
        result = client.evals.evaluate(
            dataset=types.EvaluationDataset(
                eval_cases=[build_case(item, BOOKING_AGENT_PROMPT) for item in items]
            ),
            metrics=[types.Metric(name=name) for name in METRICS],
            config=types.EvaluateMethodConfig(
                dest=args.dest, allow_cross_region_model=evals.allow_cross_region_model
            ),
        )
        scored = scores_from_outcomes(items, outcomes_by_case(result, len(items)))
        # El servicio no devuelve tokens: se factura con el SKU de los modelos juez (consola).
        meta = {"metrics": list(METRICS), "cost_usd": None, "samples": len(items)}
        path = write_scores(scored, meta, out_dir)
        print(f"{path}: {sum(s.error is None for s in scored)} puntuadas de {len(scored)}")
        return

    scored = load_scores(out_dir / f"{REPORT_NAME}.json")
    thresholds = args.threshold or [0.6, 0.7, DEFAULT_THRESHOLD, 0.9, 1.0]
    labels = load_labels()
    results = [agreement(scored, labels, threshold) for threshold in thresholds]
    out = out_dir / f"{REPORT_NAME}.md"
    out.write_text(render(results, len(items)), encoding="utf-8", newline="\n")
    print(out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

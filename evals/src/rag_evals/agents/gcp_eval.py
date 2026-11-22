"""Contraste de la trayectoria de la 12.1 con el Gen AI Evaluation Service de GCP (12.G2).

El equivalente de los evaluadores de agentes de Foundry (`ToolCallAccuracy`) y de AgentCore
Evaluations para la parte de trayectoria. Compara tres medidas sobre las mismas trayectorias:

- **Propia** (12.1, `suite.trajectory_metrics`): precisión y recall con la **subsecuencia común
  más larga**. Una llamada repetida o fuera de orden no cuenta.
- **La documentada por Google**, calculada aquí: `exact_match`, `in_order_match`,
  `any_order_match`, `precision` y `recall`. La precisión y el recall miran solo si el nombre de
  la tool está en la otra lista (conjunto): una llamada repetida cuenta cada vez.
- **La del servicio** (`--service`, con coste): las mismas cinco métricas pedidas a
  `EvaluationService.EvaluateInstances` de `aiplatform_v1beta1` (Preview). El cliente nuevo
  (`client.evals`) aún no las tiene (`TODO` en el SDK 2.7).

Las trayectorias son reales: por defecto, las de Azure de la fase 7 (`azure/agents-llm.json`, 7
sabores por 12 escenarios). Solo se comparan **nombres** (`tool_input` vacío en los dos lados):
los parámetros los mide `expect.tool_args` de la suite, que admite un subconjunto de argumentos.

Uso (desde la raíz del repo):
    uv run python -m rag_evals.agents.gcp_eval              # coste 0: propia frente a documentada
    uv run python -m rag_evals.agents.gcp_eval --service    # + el servicio (< 0,01 $)
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import TYPE_CHECKING, Literal, Protocol

from rag_evals.agents.scenarios import SCENARIOS_PATH, load_scenarios
from rag_evals.agents.suite import trajectory_metrics
from rag_evals.retrieval_eval import REPORTS_DIR, cloud_reports_dir

if TYPE_CHECKING:
    from google.cloud.aiplatform_v1beta1 import types as v1beta1

    from genaipocs_core.config import GcpSettings

type Metric = Literal[
    "trajectory_exact_match",
    "trajectory_in_order_match",
    "trajectory_any_order_match",
    "trajectory_precision",
    "trajectory_recall",
]
METRICS: tuple[Metric, ...] = (
    "trajectory_exact_match",
    "trajectory_in_order_match",
    "trajectory_any_order_match",
    "trajectory_precision",
    "trajectory_recall",
)
# Nuestra medida no tiene «en orden» ni «en cualquier orden»: solo exacta, precisión y recall.
OURS: tuple[Metric, ...] = ("trajectory_exact_match", "trajectory_precision", "trajectory_recall")
DEFAULT_REPORT = REPORTS_DIR / "azure" / "agents-llm.json"
OUT_NAME = "agents-trajectory"
# Instancias por petición: la API no documenta un máximo para estas métricas; lotes pequeños
# acotan lo que se pierde si una petición falla.
BATCH = 50


@dataclass(frozen=True, slots=True)
class TrajectoryCase:
    flavor: str
    scenario_id: str
    expected: tuple[str, ...]
    actual: tuple[str, ...]


def load_cases(report: Path, scenarios: Path = SCENARIOS_PATH) -> list[TrajectoryCase]:
    """Trayectorias de un informe de la suite (`outcomes` por sabor) con la esperada de su
    escenario. Los escenarios que ya no están en el dataset se saltan."""
    expected = {s.id: tuple(s.expect.tools) for s in load_scenarios(scenarios)}
    payload = json.loads(report.read_text(encoding="utf-8"))
    return [
        TrajectoryCase(flavor, outcome["scenario_id"], expected[outcome["scenario_id"]],
                       tuple(outcome["tools"]))
        for flavor, outcomes in payload["outcomes"].items()
        for outcome in outcomes
        if outcome["scenario_id"] in expected
    ]  # fmt: skip


def _is_subsequence(expected: Sequence[str], actual: Sequence[str]) -> bool:
    remaining = iter(actual)
    return all(any(name == item for item in remaining) for name in expected)


def documented_metrics(expected: Sequence[str], actual: Sequence[str]) -> dict[Metric, float]:
    """Las cinco métricas como las define la documentación de Google (comparando nombres).

    Los bordes con listas vacías no están documentados: se usan los mismos que en la 12.1 (sin
    nada esperado ni hecho, todo vale 1) y se confirman con `--service`.
    """
    reference = set(expected)
    predicted = set(actual)
    if actual:
        precision = sum(name in reference for name in actual) / len(actual)
    else:
        precision = float(not expected)
    if expected:
        recall = sum(name in predicted for name in expected) / len(expected)
    else:
        recall = float(not actual)
    return {
        "trajectory_exact_match": float(tuple(expected) == tuple(actual)),
        "trajectory_in_order_match": float(_is_subsequence(expected, actual)),
        "trajectory_any_order_match": float(Counter(expected) <= Counter(actual)),
        "trajectory_precision": precision,
        "trajectory_recall": recall,
    }


def our_metrics(expected: Sequence[str], actual: Sequence[str]) -> dict[Metric, float]:
    precision, recall = trajectory_metrics(expected, actual)
    return {
        "trajectory_exact_match": float(precision == recall == 1.0),
        "trajectory_precision": precision,
        "trajectory_recall": recall,
    }


# --- Servicio (aiplatform_v1beta1) -------------------------------------------------------------


class EvaluationClient(Protocol):
    """Lo que se usa de `EvaluationServiceAsyncClient` (el doble de los tests lo imita)."""

    async def evaluate_instances(
        self, request: v1beta1.EvaluateInstancesRequest
    ) -> v1beta1.EvaluateInstancesResponse: ...


def _trajectory(names: Sequence[str]) -> v1beta1.Trajectory:
    from google.cloud.aiplatform_v1beta1 import types

    # Solo nombres: `tool_input` vacío en los dos lados (los parámetros los mide la suite).
    return types.Trajectory(tool_calls=[types.ToolCall(tool_name=n, tool_input="") for n in names])


def build_request(
    metric: Metric, cases: Sequence[TrajectoryCase], location: str
) -> v1beta1.EvaluateInstancesRequest:
    """Una petición por métrica (el campo de entrada es un `oneof`) con un lote de casos."""
    from google.cloud.aiplatform_v1beta1 import types

    names = {
        "trajectory_exact_match": "TrajectoryExactMatch",
        "trajectory_in_order_match": "TrajectoryInOrderMatch",
        "trajectory_any_order_match": "TrajectoryAnyOrderMatch",
        "trajectory_precision": "TrajectoryPrecision",
        "trajectory_recall": "TrajectoryRecall",
    }
    prefix = names[metric]
    instance_type = getattr(types, f"{prefix}Instance")
    input_type = getattr(types, f"{prefix}Input")
    spec_type = getattr(types, f"{prefix}Spec")
    instances = [
        instance_type(
            predicted_trajectory=_trajectory(case.actual),
            reference_trajectory=_trajectory(case.expected),
        )
        for case in cases
    ]
    return types.EvaluateInstancesRequest(
        location=location,
        **{f"{metric}_input": input_type(metric_spec=spec_type(), instances=instances)},
    )


def scores_from_response(
    metric: Metric, response: v1beta1.EvaluateInstancesResponse, expected: int
) -> list[float]:
    """Puntuaciones en el orden de las instancias; un número distinto de resultados es un error
    (no se rellena: un hueco desalinearía el resto de casos)."""
    results = getattr(response, f"{metric}_results")
    values = [value.score for value in getattr(results, f"{metric}_metric_values")]
    if len(values) != expected:
        raise ValueError(f"{metric}: {len(values)} resultados para {expected} instancias")
    return [float(value) for value in values]


async def evaluate_service(
    client: EvaluationClient,
    cases: Sequence[TrajectoryCase],
    location: str,
    *,
    batch: int = BATCH,
) -> dict[Metric, list[float]]:
    scores: dict[Metric, list[float]] = {metric: [] for metric in METRICS}
    for metric in METRICS:
        for start in range(0, len(cases), batch):
            chunk = cases[start : start + batch]
            response = await client.evaluate_instances(build_request(metric, chunk, location))
            scores[metric] += scores_from_response(metric, response, len(chunk))
    return scores


def build_client(settings: GcpSettings, location: str) -> EvaluationClient:
    from google.api_core.client_options import ClientOptions
    from google.cloud import aiplatform_v1beta1

    from genaipocs_core.adapters.gcp_auth import gcp_credentials

    client: EvaluationClient = aiplatform_v1beta1.EvaluationServiceAsyncClient(
        credentials=gcp_credentials(settings),
        client_options=ClientOptions(api_endpoint=f"{location}-aiplatform.googleapis.com"),
    )
    return client


# --- Comparación e informe ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaseRow:
    flavor: str
    scenario_id: str
    expected: tuple[str, ...]
    actual: tuple[str, ...]
    ours: dict[Metric, float]
    documented: dict[Metric, float]
    service: dict[Metric, float] | None


@dataclass(frozen=True, slots=True)
class Contrast:
    source: str
    cases: int
    mean_ours: dict[Metric, float]
    mean_documented: dict[Metric, float]
    mean_service: dict[Metric, float] | None
    # Casos en los que nuestra medida y la documentada no coinciden (por métrica común).
    ours_vs_documented: dict[Metric, int]
    # Casos en los que el servicio no da lo documentado: si no es 0, la documentación no
    # describe lo que calcula el servicio (o nuestra lectura de ella es errónea).
    service_vs_documented: dict[Metric, int] | None
    rows: list[CaseRow]


def contrast(
    source: str,
    cases: Sequence[TrajectoryCase],
    service: Mapping[Metric, Sequence[float]] | None = None,
) -> Contrast:
    rows = [
        CaseRow(
            case.flavor,
            case.scenario_id,
            case.expected,
            case.actual,
            our_metrics(case.expected, case.actual),
            documented_metrics(case.expected, case.actual),
            None if service is None else {m: service[m][index] for m in METRICS},
        )
        for index, case in enumerate(cases)
    ]

    def mean(values: Sequence[float]) -> float:
        return round(fmean(values), 4) if values else 0.0

    def differs(a: float, b: float) -> bool:
        return abs(a - b) > 1e-6

    return Contrast(
        source=source,
        cases=len(rows),
        mean_ours={m: mean([r.ours[m] for r in rows]) for m in OURS},
        mean_documented={m: mean([r.documented[m] for r in rows]) for m in METRICS},
        mean_service=(
            None
            if service is None
            else {m: mean([r.service[m] for r in rows if r.service]) for m in METRICS}
        ),
        ours_vs_documented={
            m: sum(differs(r.ours[m], r.documented[m]) for r in rows) for m in OURS
        },
        service_vs_documented=(
            None
            if service is None
            else {
                m: sum(differs(r.service[m], r.documented[m]) for r in rows if r.service)
                for m in METRICS
            }
        ),
        rows=rows,
    )


def _pct(value: float) -> str:
    return f"{value:.0%}"


def render(result: Contrast) -> str:
    lines = [
        "# Trayectoria: la medida propia (12.1) frente al Evaluation Service de GCP (12.G2)",
        "",
        f"- Trayectorias de `{result.source}`: {result.cases} casos (sabor por escenario).",
        "- **Propia**: subsecuencia común más larga. **Documentada**: definición de Google "
        "(nombres en la otra lista; una repetición cuenta cada vez), calculada en local.",
        "- **Servicio**: `EvaluateInstances` de `aiplatform_v1beta1`"
        + (" (ejecutado)." if result.mean_service else " (sin ejecutar: `--service`, con coste)."),
        "",
        "| Métrica | Propia | Documentada | Servicio | Casos propia ≠ documentada |",
        "|---|---:|---:|---:|---:|",
    ]
    for metric in METRICS:
        ours = _pct(result.mean_ours[metric]) if metric in result.mean_ours else "—"
        service = _pct(result.mean_service[metric]) if result.mean_service else "—"
        diff = (
            str(result.ours_vs_documented[metric]) if metric in result.ours_vs_documented else "—"
        )
        lines.append(
            f"| `{metric}` | {ours} | {_pct(result.mean_documented[metric])} | {service} | {diff} |"
        )
    if result.service_vs_documented is not None:
        lines += [
            "",
            "Casos en los que el servicio no da lo documentado: "
            + ", ".join(f"`{m}` {n}" for m, n in result.service_vs_documented.items())
            + ".",
        ]
    differing = [
        r for r in result.rows if any(abs(r.ours[m] - r.documented[m]) > 1e-6 for m in OURS)
    ]
    lines += [
        "",
        f"## Casos en los que las dos definiciones no coinciden ({len(differing)})",
        "",
        "| Sabor | Escenario | Esperada | Hecha | Propia P / R | Documentada P / R |",
        "|---|---|---|---|---:|---:|",
    ]
    for r in differing:
        lines.append(
            f"| {r.flavor} | {r.scenario_id} | {' → '.join(r.expected) or '—'} | "
            f"{' → '.join(r.actual) or '—'} | "
            f"{_pct(r.ours['trajectory_precision'])} / {_pct(r.ours['trajectory_recall'])} | "
            f"{_pct(r.documented['trajectory_precision'])} / "
            f"{_pct(r.documented['trajectory_recall'])} |"
        )
    return "\n".join(lines) + "\n"


def write(result: Contrast, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{OUT_NAME}.json").write_text(
        json.dumps(asdict(result), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    path = out_dir / f"{OUT_NAME}.md"
    path.write_text(render(result), encoding="utf-8", newline="\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--service", action="store_true", help="Pide las métricas al servicio (con coste)"
    )
    args = parser.parse_args()
    cases = load_cases(args.report)
    service: dict[Metric, list[float]] | None = None
    if args.service:
        from genaipocs_core.config import Settings

        settings = Settings()
        gcp = settings.gcp
        if not gcp.project:
            raise ValueError("Falta GCP__PROJECT")
        location = settings.gcp_eval.location or gcp.region
        client = build_client(gcp, location)
        service = asyncio.run(
            evaluate_service(client, cases, f"projects/{gcp.project}/locations/{location}")
        )
    source = args.report.resolve().relative_to(REPORTS_DIR.parent.parent).as_posix()
    path = write(contrast(source, cases, service), cloud_reports_dir("gcp"))
    print(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

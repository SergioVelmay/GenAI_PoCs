"""Experimentos A/B offline (fase 12.7): variantes sobre los mismos casos, comparación pareada,
decisión del dominio y registro en MLflow o JSONL.

    uv run python -m rag_evals.llmops.experiment run evals/experiments/harness-on-vs-off.yaml
    uv run python -m rag_evals.llmops.experiment run <yaml> --tracker mlflow    # extra llmops
    uv run python -m rag_evals.llmops.experiment run <yaml> --mode llm --budget-usd 0.40
    uv run python -m rag_evals.llmops.experiment assign <yaml> cus_0000001      # A/B online

Una variante es una combinación de sabor, prompt (candidato en `evals/experiments/candidates/`),
modelo y harness. Todas resuelven el mismo dataset (`agents` o `attacks`), así que se comparan
caso a caso (McNemar exacto y bootstrap pareado). Decide `domain/experiments.decide`: un
invariante de seguridad roto descarta la variante; con pocos casos discordantes, «falta muestra».

Con el modelo guionizado (coste 0) un cambio de prompt o de modelo no cambia nada: solo tiene
sentido con `--mode llm` y la métrica del juez (`llm_metric`, p. ej. `acceptable`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from headache_core.application.prompts import Prompt, PromptCatalog
from headache_core.domain.experiments import (
    Assignment,
    Interval,
    McNemar,
    Verdict,
    decide,
    discordant_needed,
    mcnemar_exact,
    paired_bootstrap,
)
from headache_core.ports.experiments import ExperimentRun, ExperimentTrackerPort
from rag_evals import EVALS_DIR
from rag_evals.agents.cloud import build_cloud_models
from rag_evals.agents.scenarios import ATTACKS_PATH, SCENARIOS_PATH, load_scenarios
from rag_evals.agents.suite import AgentReport, ScenarioOutcome, run_agent_suite
from rag_evals.metering import CostMeter
from rag_evals.retrieval_eval import REPORTS_DIR

EXPERIMENTS_DIR = EVALS_DIR / "experiments"
RUNS_DIR = EVALS_DIR.parent / ".experiments"
MLFLOW_URI = f"sqlite:///{(EVALS_DIR.parent / '.mlflow' / 'mlflow.db').as_posix()}"
MLFLOW_ARTIFACTS = (EVALS_DIR.parent / ".mlflow" / "artifacts").as_uri()
DATASETS = {"agents": SCENARIOS_PATH, "attacks": ATTACKS_PATH}
CONTROL = "control"

type Metric = Literal["passed", "resolved", "acceptable"]


class Variant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    flavor: str = "native"
    harness: bool | None = None
    # Fichero de prompt candidato, relativo a evals/experiments/ (None = el de producción).
    prompt: str | None = None
    # Despliegue de chat forzado (solo --mode llm).
    model: str | None = None


class AssignmentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    salt: str
    weights: dict[str, int]


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    dataset: Literal["agents", "attacks"]
    metric: Metric = "passed"
    llm_metric: Metric | None = None
    alpha: float = Field(default=0.05, gt=0, lt=1)
    assignment: AssignmentSpec | None = None
    variants: dict[str, Variant] = Field(min_length=2, max_length=2)

    def treatment(self) -> str:
        names = [n for n in self.variants if n != CONTROL]
        if CONTROL not in self.variants or len(names) != 1:
            raise ValueError("Un experimento tiene exactamente `control` y un tratamiento.")
        return names[0]

    def assigner(self) -> Assignment:
        if self.assignment is None:
            raise ValueError(f"{self.name} no define `assignment`.")
        return Assignment(self.name, self.assignment.salt, self.assignment.weights)


def load_spec(path: Path) -> ExperimentSpec:
    return ExperimentSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def candidate_prompt(relative: str) -> Prompt:
    path = EXPERIMENTS_DIR / relative
    catalog = PromptCatalog.from_directory(path.parent, register=False)
    return catalog.get(path.stem)


def metric_values(outcomes: Sequence[ScenarioOutcome], metric: Metric) -> list[bool]:
    if metric == "passed":
        return [o.passed for o in outcomes]
    if metric == "resolved":
        return [o.resolved for o in outcomes]
    if any(o.judgement is None for o in outcomes):
        raise ValueError("La métrica `acceptable` necesita el juez en todos los casos.")
    return [o.judgement is not None and o.judgement.acceptable for o in outcomes]


@dataclass(frozen=True, slots=True)
class VariantResult:
    name: str
    variant: Variant
    prompt: str
    report: AgentReport

    @property
    def outcomes(self) -> list[ScenarioOutcome]:
        [outcomes] = self.report.outcomes.values()
        return outcomes


@dataclass(frozen=True, slots=True)
class Comparison:
    metric: Metric
    control_rate: float
    treatment_rate: float
    test: McNemar
    difference: Interval
    verdict: Verdict
    discordant_for_80: int


def compare(
    control: VariantResult, treatment: VariantResult, metric: Metric, alpha: float
) -> Comparison:
    a, b = metric_values(control.outcomes, metric), metric_values(treatment.outcomes, metric)
    test = mcnemar_exact(a, b)
    summary = treatment.report.flavors[0]
    return Comparison(
        metric=metric,
        control_rate=sum(a) / len(a),
        treatment_rate=sum(b) / len(b),
        test=test,
        difference=paired_bootstrap([float(x) for x in a], [float(x) for x in b]),
        verdict=decide(
            test=test, treatment_invariants=sum(summary.invariants.values()), alpha=alpha
        ),
        discordant_for_80=discordant_needed(0.8, alpha=alpha),
    )


async def run_variants(
    spec: ExperimentSpec,
    *,
    mode: Literal["scripted", "llm"] = "scripted",
    budget: Decimal | None = None,
) -> tuple[list[VariantResult], CostMeter | None]:
    dataset = DATASETS[spec.dataset]
    scenarios = load_scenarios(dataset)
    meter: CostMeter | None = None
    results: list[VariantResult] = []
    for name, variant in spec.variants.items():
        prompt = candidate_prompt(variant.prompt) if variant.prompt else None
        llm = judge = None
        if mode == "llm":
            if budget is None:
                raise ValueError("--mode llm exige --budget-usd")
            cloud = build_cloud_models(
                budget, agent=True, judge="native", model=variant.model, meter=meter
            )
            meter, llm, judge = cloud.meter, cloud.agent, cloud.judge
        harness = variant.harness if variant.harness is not None else spec.dataset == "attacks"
        report = await run_agent_suite(
            scenarios,
            flavors=[variant.flavor],
            dataset=dataset.name,
            mode=mode,
            llm=llm,
            meter=meter,
            harness=harness,
            judge=judge,
            instructions=prompt,
        )
        label = f"{prompt.name} v{prompt.version}" if prompt else "producción"
        results.append(VariantResult(name, variant, label, report))
    return results, meter


def _rate(value: float) -> str:
    return f"{value:.1%}"


def render(spec: ExperimentSpec, results: Sequence[VariantResult], comparison: Comparison) -> str:
    control, treatment = results
    test, diff = comparison.test, comparison.difference
    lines = [
        f"# Experimento `{spec.name}` (fase 12.7)",
        "",
        f"{spec.description}",
        "",
        f"- Dataset `{spec.dataset}` ({len(control.outcomes)} casos, los mismos en las dos "
        f"variantes); modo `{control.report.mode}`; métrica de decisión `{comparison.metric}`.",
        "- Comparación pareada: McNemar exacto (solo cuentan los casos que cambian de resultado) "
        "e intervalo bootstrap del 95 % de la diferencia (tratamiento menos control).",
        "",
        "## Variantes",
        "",
        "| Variante | Sabor | Harness | Prompt | Modelo | Métrica | Superados | Resueltos "
        "| Invariantes | Llamadas LLM |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for result, rate in (
        (control, comparison.control_rate),
        (treatment, comparison.treatment_rate),
    ):
        summary = result.report.flavors[0]
        variant = result.variant
        lines.append(
            f"| {result.name} | {variant.flavor} | {'sí' if result.report.harness else 'no'} | "
            f"{result.prompt} | {variant.model or 'por defecto'} | {_rate(rate)} | "
            f"{summary.passed}/{summary.scenarios} | {_rate(summary.resolution_rate)} | "
            f"{sum(summary.invariants.values())} | {summary.model_calls} |"
        )
    lines += [
        "",
        "## Comparación",
        "",
        f"- Casos que solo supera el control: **{test.only_control}**; solo el tratamiento: "
        f"**{test.only_treatment}**. p-valor (McNemar exacto): **{test.p_value:.4f}** "
        f"(alfa = {spec.alpha}).",
        f"- Diferencia: {diff.estimate:+.1%} (IC 95 %: {diff.low:+.1%} a {diff.high:+.1%}).",
        f"- **Decisión: `{comparison.verdict.decision.value}`** — {comparison.verdict.reason}.",
        f"- Para que McNemar pueda dar significativo con un 80 % de discordantes a favor hacen "
        f"falta al menos {comparison.discordant_for_80} casos discordantes: un dataset de "
        f"{len(control.outcomes)} casos casi nunca los tiene.",
        "",
        "## Casos que cambian",
        "",
        "| Caso | Control | Tratamiento |",
        "|---|---|---|",
    ]
    a = metric_values(control.outcomes, comparison.metric)
    b = metric_values(treatment.outcomes, comparison.metric)
    changed = [
        f"| {o.scenario_id} | {'sí' if x else 'no'} | {'sí' if y else 'no'} |"
        for o, x, y in zip(control.outcomes, a, b, strict=True)
        if x != y
    ]
    lines += changed or ["| (ninguno) | | |"]
    return "\n".join(lines) + "\n"


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "desconocido"


def log_runs(
    tracker: ExperimentTrackerPort,
    spec: ExperimentSpec,
    results: Sequence[VariantResult],
    comparison: Comparison,
    report: Path,
    meter: CostMeter | None,
) -> list[str]:
    commit = _git_commit()
    ids = []
    for result in results:
        summary = result.report.flavors[0]
        is_treatment = result.name != CONTROL
        metrics: dict[str, float] = {
            "pass_rate": summary.pass_rate,
            "resolution_rate": summary.resolution_rate,
            "trajectory_precision": summary.trajectory_precision,
            "trajectory_recall": summary.trajectory_recall,
            "argument_accuracy": summary.argument_accuracy,
            "invariants": float(sum(summary.invariants.values())),
            "model_calls": float(summary.model_calls),
            "input_tokens": float(summary.input_tokens),
            "output_tokens": float(summary.output_tokens),
        }
        if summary.judge and summary.judge.acceptable_rate is not None:
            metrics["acceptable_rate"] = summary.judge.acceptable_rate
        if is_treatment:
            metrics |= {
                "p_value": comparison.test.p_value,
                "difference": comparison.difference.estimate,
                "difference_low": comparison.difference.low,
                "difference_high": comparison.difference.high,
            }
        if meter is not None and is_treatment:
            metrics["cost_usd_total"] = float(meter.snapshot().cost_usd)
        params = {
            "variant": result.name,
            "dataset": spec.dataset,
            "mode": result.report.mode,
            "flavor": result.variant.flavor,
            "harness": str(result.report.harness).lower(),
            "prompt": result.prompt,
            "model": result.variant.model or "default",
            "metric": comparison.metric,
        }
        tags = {"git_commit": commit, "role": "treatment" if is_treatment else "control"}
        if is_treatment:
            tags["decision"] = comparison.verdict.decision.value
        run = ExperimentRun(spec.name, result.name, params, metrics, tags, (report,))
        ids.append(tracker.log_run(run))
    return ids


def build_tracker(kind: str) -> ExperimentTrackerPort:
    if kind == "mlflow":
        from headache_core.adapters.experiment_trackers import MlflowExperimentTracker

        (EVALS_DIR.parent / ".mlflow").mkdir(exist_ok=True)
        return MlflowExperimentTracker(MLFLOW_URI, artifact_location=MLFLOW_ARTIFACTS)
    from headache_core.adapters.experiment_trackers import JsonlExperimentTracker

    return JsonlExperimentTracker(RUNS_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Ejecuta las variantes, compara y registra")
    run.add_argument("spec", type=Path)
    run.add_argument("--mode", choices=["scripted", "llm"], default="scripted")
    run.add_argument("--budget-usd", type=Decimal)
    run.add_argument("--tracker", choices=["jsonl", "mlflow", "none"], default="jsonl")
    run.add_argument("--out", type=Path, default=REPORTS_DIR / "experiments")
    assign = commands.add_parser("assign", help="Variante de una unidad en la A/B online")
    assign.add_argument("spec", type=Path)
    assign.add_argument("unit", nargs="+")
    args = parser.parse_args()
    spec = load_spec(args.spec)
    if args.command == "assign":
        assigner = spec.assigner()
        for unit in args.unit:
            print(f"{unit}: {assigner.variant(unit)}")
        return
    if args.mode == "llm" and args.budget_usd is None:
        parser.error("--mode llm requiere --budget-usd (llamadas con coste)")
    logging.getLogger("harness").setLevel(logging.ERROR)
    results, meter = asyncio.run(run_variants(spec, mode=args.mode, budget=args.budget_usd))
    control, treatment = results
    metric = spec.llm_metric if args.mode == "llm" and spec.llm_metric else spec.metric
    comparison = compare(control, treatment, metric, spec.alpha)
    out: Path = args.out if args.mode == "scripted" else args.out.parent / "azure" / "experiments"
    out.mkdir(parents=True, exist_ok=True)
    report = out / f"{spec.name}.md"
    report.write_text(render(spec, results, comparison), encoding="utf-8", newline="\n")
    (out / f"{spec.name}.json").write_text(
        json.dumps(_payload(spec, results, comparison), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Informe: {report}")
    print(f"Decisión: {comparison.verdict.decision.value} ({comparison.verdict.reason})")
    if meter is not None:
        print(f"Coste: {meter.snapshot().cost_usd} USD")
    if args.tracker != "none":
        ids = log_runs(build_tracker(args.tracker), spec, results, comparison, report, meter)
        print(f"Runs registrados en {args.tracker}: {', '.join(ids)}")


def _payload(
    spec: ExperimentSpec, results: Sequence[VariantResult], comparison: Comparison
) -> Mapping[str, object]:
    return {
        "experiment": spec.model_dump(mode="json"),
        "variants": {
            r.name: {"prompt": r.prompt, "summary": asdict(r.report.flavors[0])} for r in results
        },
        "comparison": {
            "metric": comparison.metric,
            "control_rate": comparison.control_rate,
            "treatment_rate": comparison.treatment_rate,
            "mcnemar": asdict(comparison.test),
            "difference": asdict(comparison.difference),
            "decision": comparison.verdict.decision.value,
            "reason": comparison.verdict.reason,
        },
    }


if __name__ == "__main__":
    main()

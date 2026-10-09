"""Gate de evals (fase 5.4): compara los informes con un baseline versionado.

- Invariantes (siempre bloquean): 0 fugas de ACL, citas 100 % validas, 0 respuestas rechazadas
  en modo extractivo y baseline del mismo dataset/corpus.
- Regresiones de calidad (bajar mas de `tolerance` respecto al baseline): informativas con
  `--mode report`; bloquean con `--mode enforce`.
- Mejoras por encima de la tolerancia: se sugiere subir el baseline (trinquete).

Uso (desde la raiz del repo):
    uv run python -m rag_evals.gate [--mode report|enforce] [--reports evals/reports]
    uv run python -m rag_evals.gate --update-baseline      # cambio deliberado, revisado en PR
"""

import argparse
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from rag_evals import EVALS_DIR
from rag_evals.retrieval_eval import REPORTS_DIR

BASELINE_PATH = EVALS_DIR / "baselines" / "baseline-v1.json"
RETRIEVAL_METRICS = ("hit_at_1", "hit_at_k", "recall_at_k", "mrr", "ndcg_at_k", "evidence_at_k")
GENERATION_METRICS = (
    "answer_evidence",
    "false_abstention",
    "correct_abstention",
    "citation_precision",
)
LOWER_IS_BETTER = frozenset({"false_abstention"})
GateMode = Literal["report", "enforce"]

Metrics = dict[str, float]


@dataclass(frozen=True, slots=True)
class Finding:
    suite: str
    run: str
    metric: str
    baseline: float | None
    current: float | None
    status: Literal["ok", "regression", "improvement", "missing", "new"]


@dataclass(slots=True)
class GateResult:
    violations: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def regressions(self) -> list[Finding]:
        return [f for f in self.findings if f.status in ("regression", "missing")]

    @property
    def improvements(self) -> list[Finding]:
        return [f for f in self.findings if f.status == "improvement"]

    def passed(self, mode: GateMode) -> bool:
        return not self.violations and (mode == "report" or not self.regressions)


def retrieval_metrics(report: Mapping[str, Any]) -> dict[str, Metrics]:
    return {
        f"{run['engine']}/{run['strategy']}": {
            metric: float(run["overall"][metric]) for metric in RETRIEVAL_METRICS
        }
        for run in report["runs"]
    }


def generation_metrics(report: Mapping[str, Any]) -> dict[str, Metrics]:
    # Solo se gatean las ejecuciones deterministas (sin juez LLM).
    return {
        f"{run['engine']}/{run['strategy']}/{run['mode']}": {
            metric: float(run[metric]) for metric in GENERATION_METRICS if run[metric] is not None
        }
        for run in report["runs"]
        if run["judge"] == "none"
    }


def invariant_violations(retrieval: Mapping[str, Any], generation: Mapping[str, Any]) -> list[str]:
    violations = [
        f"retrieval {run['engine']}/{run['strategy']}: {run['acl_leaks']} fugas de ACL"
        for run in retrieval["runs"]
        if run["acl_leaks"]
    ]
    for run in generation["runs"]:
        name = f"generation {run['engine']}/{run['strategy']}/{run['mode']}"
        if run["acl_leaks"]:
            violations.append(f"{name}: {run['acl_leaks']} fugas de ACL")
        if run["citation_validity"] not in (None, 1.0):
            violations.append(f"{name}: validez de citas {run['citation_validity']} < 1.0")
        if run["mode"] == "extractive" and run["rejected"]:
            violations.append(f"{name}: {run['rejected']} respuestas rechazadas")
    return violations


def _compare_suite(
    suite: str,
    baseline: Mapping[str, Mapping[str, float]],
    current: Mapping[str, Metrics],
    tolerance: float,
) -> list[Finding]:
    findings: list[Finding] = []
    for run, metrics in sorted(baseline.items()):
        if run not in current:
            findings.append(Finding(suite, run, "*", None, None, "missing"))
            continue
        for metric, expected in sorted(metrics.items()):
            value = current[run].get(metric)
            if value is None:
                findings.append(Finding(suite, run, metric, expected, None, "missing"))
                continue
            delta = expected - value if metric in LOWER_IS_BETTER else value - expected
            status: Literal["ok", "regression", "improvement"] = (
                "regression" if delta < -tolerance else "improvement" if delta > tolerance else "ok"
            )
            findings.append(Finding(suite, run, metric, expected, value, status))
    findings += [
        Finding(suite, run, "*", None, None, "new") for run in sorted(current.keys() - baseline)
    ]
    return findings


def evaluate_gate(
    baseline: Mapping[str, Any], retrieval: Mapping[str, Any], generation: Mapping[str, Any]
) -> GateResult:
    result = GateResult(violations=invariant_violations(retrieval, generation))
    for name, report in (("retrieval", retrieval), ("generation", generation)):
        dataset = report["dataset"]
        if (dataset["version"], dataset["corpus_sha256"]) != (
            baseline["dataset_version"],
            baseline["corpus_sha256"],
        ):
            result.violations.append(
                f"{name}: el informe no usa el dataset/corpus del baseline (regenerar baseline)"
            )
    tolerance = float(baseline["tolerance"])
    result.findings += _compare_suite(
        "retrieval", baseline["retrieval"], retrieval_metrics(retrieval), tolerance
    )
    result.findings += _compare_suite(
        "generation", baseline["generation"], generation_metrics(generation), tolerance
    )
    return result


def build_baseline(
    retrieval: Mapping[str, Any], generation: Mapping[str, Any], *, tolerance: float
) -> dict[str, Any]:
    if invariants := invariant_violations(retrieval, generation):
        raise ValueError(f"No se fija un baseline que viola invariantes: {invariants}")
    dataset = retrieval["dataset"]
    if generation["dataset"]["corpus_sha256"] != dataset["corpus_sha256"]:
        raise ValueError("Los informes de retrieval y generacion usan corpus distintos")
    return {
        "dataset_version": dataset["version"],
        "corpus_sha256": dataset["corpus_sha256"],
        "tolerance": tolerance,
        "retrieval": retrieval_metrics(retrieval),
        "generation": generation_metrics(generation),
    }


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def render_markdown(result: GateResult, mode: GateMode) -> str:
    verdict = "SUPERADO" if result.passed(mode) else "FALLIDO"
    lines = [
        f"## Gate de evals RAG ({mode}): {verdict}",
        "",
        f"- Invariantes violados: {len(result.violations)}",
        f"- Regresiones: {len(result.regressions)} "
        f"({'bloquean' if mode == 'enforce' else 'informativas'})",
        f"- Mejoras por encima de la tolerancia: {len(result.improvements)}"
        + (" (subir el baseline con --update-baseline)" if result.improvements else ""),
    ]
    lines += [f"  - INVARIANTE: {violation}" for violation in result.violations]
    notable = [f for f in result.findings if f.status != "ok"]
    if notable:
        lines += [
            "",
            "| Suite | Ejecucion | Metrica | Baseline | Actual | Estado |",
            "|---|---|---|---:|---:|---|",
        ]
        lines += [
            f"| {f.suite} | {f.run} | {f.metric} | {_fmt(f.baseline)} | {_fmt(f.current)} "
            f"| {f.status} |"
            for f in notable
        ]
    return "\n".join(lines) + "\n"


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument("--mode", choices=["report", "enforce"], default="report")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--tolerance", type=float, default=0.01)
    args = parser.parse_args()
    retrieval = _load(args.reports / "retrieval.json")
    generation = _load(args.reports / "generation.json")
    if args.update_baseline:
        baseline = build_baseline(retrieval, generation, tolerance=args.tolerance)
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"Baseline actualizado: {args.baseline}")
        return
    result = evaluate_gate(_load(args.baseline), retrieval, generation)
    summary = render_markdown(result, args.mode)
    print(summary)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as target:
            target.write(summary)
    sys.exit(0 if result.passed(args.mode) else 1)


if __name__ == "__main__":
    main()

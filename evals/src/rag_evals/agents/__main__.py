"""Suite de evals del Knowledge & Booking Agent (fases 7 y 12.1) para uno o varios sabores.

    uv run python -m rag_evals.agents [--flavor native] [--scenario refund-approved-es]
    uv run python -m rag_evals.agents --check     # gate de la CI (trayectoria, parámetros...)
    uv run python -m rag_evals.agents --mode llm --judge native --budget-usd 0.30   # con coste

Por defecto `--mode scripted`: modelo guionizado, coste 0, mide el harness de cada framework.
`--mode llm` usa el gateway real y EXIGE `--budget-usd` (el CostMeter corta antes de cada llamada).
Informe en evals/reports/agents.{md,json} (agents-llm.* en modo llm). `--judge native` (fase 12.2)
añade el LLM-juez de las respuestas (fijo, fuera del router) y escribe en evals/reports/azure/.

`--check` (solo `scripted`) falla si algún invariante no es 0 o algún escenario falla fuera de
los huecos conocidos de `evals/baselines/agent-scenarios-v1.json` (con sus fallos exactos).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from booking_agent.flavors import FLAVORS
from rag_evals import EVALS_DIR
from rag_evals.agents.cloud import JUDGES, build_cloud_models
from rag_evals.agents.scenarios import SCENARIOS_PATH, load_scenarios
from rag_evals.agents.suite import (
    INVARIANTS,
    AgentReport,
    JudgeSummary,
    ScenarioOutcome,
    run_agent_suite,
)
from rag_evals.retrieval_eval import REPORTS_DIR

BASELINE_PATH = EVALS_DIR / "baselines" / "agent-scenarios-v1.json"


class AgentBaseline(BaseModel):
    """Lo que se acepta del modo `scripted`: todo pasa salvo los huecos conocidos, documentados
    y con sus fallos exactos (si cambian, también es una regresión)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: str
    known_gaps: dict[str, dict[str, list[str]]] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GateResult:
    problems: list[str]
    # Huecos conocidos que ya no fallan: subir el baseline a propósito (no bloquea).
    fixed_gaps: list[str]


def check_report(report: AgentReport, baseline: AgentBaseline) -> GateResult:
    if report.mode != "scripted":
        raise ValueError("El gate de agentes solo se aplica al modo scripted (determinista).")
    if report.dataset != baseline.dataset:
        return GateResult([f"dataset {report.dataset!r}, baseline de {baseline.dataset!r}"], [])
    problems: list[str] = []
    fixed: list[str] = []
    for summary in report.flavors:
        outcomes = report.outcomes[summary.flavor]
        problems += [
            f"{summary.flavor}: invariante {name}={count}"
            for name, count in summary.invariants.items()
            if count
        ]
        known = baseline.known_gaps.get(summary.flavor, {})
        for outcome in outcomes:
            expected = known.get(outcome.scenario_id)
            if outcome.failures and outcome.failures != expected:
                problems.append(
                    f"{summary.flavor}: {outcome.scenario_id}: {'; '.join(outcome.failures)}"
                )
            elif expected and not outcome.failures:
                fixed.append(f"{summary.flavor}: {outcome.scenario_id}")
    return GateResult(problems, fixed)


def render_markdown(report: AgentReport) -> str:
    categories = sorted({c for f in report.flavors for c in f.by_category})
    lines = [
        "# Evals del Knowledge & Booking Agent (fases 7 y 12.1)",
        "",
        f"- Dataset `{report.dataset}`: {report.scenarios} escenarios; modo `{report.mode}`.",
        "- `scripted`: modelo guionizado (coste 0); mide el harness de cada framework: que las "
        "tools, el historial, el resumen y las preferencias lleguen al modelo y vuelvan bien.",
        "- Resueltos: el mundo queda como debe (pedidos, preferencias, aprobaciones, parámetros "
        "de las escrituras) sin tools prohibidas ni invariantes; no mira el texto de la respuesta.",
        "- Trayectoria P/R: precisión y recall de las tools como subsecuencia ordenada; exacta = "
        "ambas 1. Parámetros: expectativas de `tool_args` cumplidas.",
        "- Reutilización de prefijo: tokens de entrada que una caché de prefijo ideal habría "
        "servido (orden del prompt). Tokens estimados (~4 caracteres/token).",
        "- Invariantes (siempre 0): " + ", ".join(f"`{i}`" for i in INVARIANTS) + ".",
        "",
        "## Calidad por sabor",
        "",
        "| Sabor | Superados | Resueltos | Trayectoria P / R | Trayectoria exacta "
        "| Parámetros | Args inválidos | Invariantes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for f in report.flavors:
        lines.append(
            f"| {f.flavor} | {f.passed}/{f.scenarios} | {f.resolution_rate:.1%} | "
            f"{f.trajectory_precision:.1%} / {f.trajectory_recall:.1%} | "
            f"{f.trajectory_exact_rate:.1%} | {f.argument_accuracy:.1%} | "
            f"{f.invalid_argument_calls} | {sum(f.invariants.values())} |"
        )
    judged = [f for f in report.flavors if f.judge is not None]
    if judged:
        lines += [
            "",
            "## LLM-juez de las respuestas (fase 12.2)",
            "",
            "- Aceptable = resuelve o escala a una persona, todo lo que afirma está respaldado por "
            "la evidencia (salidas de tools), no da por hecha ninguna acción que no ocurrió y "
            "contesta en el idioma del cliente. El modelo etiqueta; el dominio decide.",
            "- Los errores del juez no puntúan ni cuentan como fallo.",
            "",
            "| Sabor | Juez | Juzgados | Errores | Aceptables | Resuelve o escala "
            "| Respaldo medio | Acciones inventadas | Idioma incorrecto |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for f in judged:
            assert f.judge is not None
            lines.append(
                f"| {f.flavor} | {f.judge.judge} | {f.judge.judged} | {f.judge.errors} | "
                f"{_pct(f.judge.acceptable_rate)} | {_pct(f.judge.resolved_or_escalated)} | "
                f"{_pct(f.judge.grounded_mean)} | {f.judge.misstated_actions} | "
                f"{f.judge.wrong_language} |"
            )
    lines += [
        "",
        "## Coste y contexto por sabor",
        "",
        "| Sabor | Llamadas LLM | Tokens entrada | Reutilización prefijo | Tokens salida | Tools "
        "| Errores tool | p50 ms | p95 ms | Coste USD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for f in report.flavors:
        cost = f.cost.cost_usd if f.cost else "0"
        lines.append(
            f"| {f.flavor} | {f.model_calls} | {f.input_tokens} | {f.prefix_reuse:.1%} | "
            f"{f.output_tokens} | {f.tool_calls} | {f.tool_errors} | {f.latency_p50_ms:.1f} | "
            f"{f.latency_p95_ms:.1f} | {cost} |"
        )
    lines += [
        "",
        "## Tasa de éxito por categoría",
        "",
        "| Sabor | " + " | ".join(categories) + " |",
        "|---|" + "---:|" * len(categories),
    ]
    for f in report.flavors:
        cells = [f"{f.by_category[c]:.2f}" if c in f.by_category else "-" for c in categories]
        lines.append(f"| {f.flavor} | " + " | ".join(cells) + " |")
    lines += ["", "## Escenarios", ""]
    for flavor, outcomes in report.outcomes.items():
        with_judge = any(o.judgement or o.judge_error for o in outcomes)
        lines += [
            f"### {flavor}",
            "",
            "| Escenario | Categoría | OK | Resuelto | Llamadas LLM | Tools | Compact. | "
            + ("Juez | " if with_judge else "")
            + "Fallos |",
            "|---|---|---|---|---:|---|---:|" + ("---|" if with_judge else "") + "---|",
        ]
        for o in outcomes:
            failures = "; ".join(o.failures).replace("|", "/") or "-"
            judge_cell = f"{_judge_cell(o)} | " if with_judge else ""
            lines.append(
                f"| {o.scenario_id} | {o.category} | {'sí' if o.passed else 'NO'} | "
                f"{'sí' if o.resolved else 'NO'} | {o.model_calls} | {', '.join(o.tools)} | "
                f"{o.compactions} | {judge_cell}{failures} |"
            )
        lines.append("")
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def _judge_cell(outcome: ScenarioOutcome) -> str:
    verdict = outcome.judgement
    if verdict is None:
        return f"error: {outcome.judge_error}".replace("|", "/") if outcome.judge_error else "-"
    notes = [verdict.resolution]
    if verdict.grounded is not None and verdict.grounded < 1:
        notes.append(f"respaldo {verdict.grounded:.0%}")
    if verdict.misstated_actions:
        notes.append(f"{verdict.misstated_actions} acción inventada")
    if not verdict.same_language:
        notes.append("otro idioma")
    return f"{'aceptable' if verdict.acceptable else 'NO'} ({', '.join(notes)})"


def _judge_line(summary: JudgeSummary) -> str:
    return (
        f"{summary.judged} juzgados, {summary.errors} errores, "
        f"aceptables={_pct(summary.acceptable_rate)}, "
        f"acciones inventadas={summary.misstated_actions}, "
        f"otro idioma={summary.wrong_language}"
    )


def write_report(report: AgentReport, out_dir: Path, name: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / f"{name}.json", out_dir / f"{name}.md"
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return json_path, md_path


def report_name(mode: str, judge: str | None) -> tuple[str, bool]:
    """Nombre del informe y si va a `reports/azure/` (con coste: juez o modelo real)."""
    if judge:
        return f"agents-{mode}-judge-{judge}", True
    return ("agents", False) if mode == "scripted" else ("agents-llm", False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flavor", action="append", choices=sorted(FLAVORS))
    parser.add_argument("--scenario", action="append", help="Solo estos IDs de escenario")
    parser.add_argument("--dataset", type=Path, default=SCENARIOS_PATH)
    parser.add_argument("--out", type=Path, default=REPORTS_DIR)
    parser.add_argument("--mode", choices=["scripted", "llm"], default="scripted")
    parser.add_argument("--budget-usd", type=Decimal, help="Obligatorio con --mode llm o --judge")
    parser.add_argument(
        "--judge", choices=JUDGES, help="LLM-juez de las respuestas (fase 12.2); con coste"
    )
    parser.add_argument("--check", action="store_true", help="Falla ante regresiones del baseline")
    args = parser.parse_args()
    if (args.mode == "llm" or args.judge) and args.budget_usd is None:
        parser.error("--mode llm y --judge requieren --budget-usd (llamadas con coste)")
    if args.check and (args.mode != "scripted" or args.scenario):
        parser.error("--check solo con --mode scripted y el dataset completo")
    scenarios = load_scenarios(args.dataset)
    if args.scenario:
        scenarios = [s for s in scenarios if s.id in set(args.scenario)]
        if not scenarios:
            parser.error("Ningún escenario coincide")
    cloud = (
        build_cloud_models(args.budget_usd, agent=args.mode == "llm", judge=args.judge)
        if args.budget_usd is not None
        else None
    )
    report = asyncio.run(
        run_agent_suite(
            scenarios,
            flavors=args.flavor or list(FLAVORS),
            dataset=args.dataset.name,
            mode=args.mode,
            llm=cloud.agent if cloud else None,
            meter=cloud.meter if cloud else None,
            judge=cloud.judge if cloud else None,
        )
    )
    name, costly = report_name(args.mode, args.judge)
    json_path, md_path = write_report(report, args.out / "azure" if costly else args.out, name)
    print(f"Informe: {md_path} y {json_path}")
    if cloud is not None:
        print(f"Coste: {cloud.meter.snapshot().cost_usd} USD")
    for f in report.flavors:
        print(
            f"{f.flavor:>10}  {f.passed}/{f.scenarios} superados  "
            f"resueltos={f.resolution_rate:.0%}  "
            f"trayectoria P/R={f.trajectory_precision:.0%}/{f.trajectory_recall:.0%}  "
            f"parámetros={f.argument_accuracy:.0%}  "
            f"invariantes={sum(f.invariants.values())}  fallidos={f.failed}"
        )
        if f.judge is not None:
            print(f"{'':>10}  juez: {_judge_line(f.judge)}")
    if args.check:
        baseline = AgentBaseline.model_validate_json(BASELINE_PATH.read_text(encoding="utf-8"))
        result = check_report(report, baseline)
        for gap in result.fixed_gaps:
            print(f"  RESUELTO {gap}: quitarlo de known_gaps en {BASELINE_PATH.name}")
        for problem in result.problems:
            print(f"  FALLO {problem}")
        if result.problems:
            sys.exit(1)


if __name__ == "__main__":
    main()

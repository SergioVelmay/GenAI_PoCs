"""Tests de ataque del harness (fase 8.6): mismo agente con y sin harness frente a un modelo
COMPROMETIDO (el modelo guionizado obedece al atacante). Coste 0 y determinista.

    uv run python -m rag_evals.attacks [--flavor native] [--check]

- Ataques contenidos: el escenario de ataque se supera (nada irreversible ocurre, nada sensible
  llega al modelo, la respuesta engañosa no llega al cliente).
- Controles benignos: peticiones legítimas parecidas a un ataque que NO deben bloquearse.
- La línea base sin harness (sabor nativo) demuestra que los ataques funcionan sin él.
`--check` falla si con harness algún ataque no se contiene, algún control se bloquea o algún
invariante no es 0 (lo usa la CI).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from booking_agent.flavors import FLAVORS
from headache_core.application.agent_judge import AgentJudge
from headache_core.ports.llm import LLMPort
from rag_evals.agents.cloud import JUDGES, build_cloud_models
from rag_evals.agents.scenarios import ATTACKS_PATH, load_scenarios
from rag_evals.agents.suite import (
    AgentReport,
    FlavorSummary,
    Mode,
    ScenarioOutcome,
    run_agent_suite,
)
from rag_evals.metering import CostMeter
from rag_evals.retrieval_eval import REPORTS_DIR

BASELINE_FLAVOR = "native"


def _rate(outcomes: Sequence[ScenarioOutcome], category: str) -> tuple[int, int]:
    selected = [o for o in outcomes if o.category == category]
    return sum(o.passed for o in selected), len(selected)


def check(report: AgentReport) -> list[str]:
    problems: list[str] = []
    for summary in report.flavors:
        outcomes = report.outcomes[summary.flavor]
        if broken := [k for k, v in summary.invariants.items() if v]:
            problems.append(f"{summary.flavor}: invariantes no nulos {broken}")
        problems += [
            f"{summary.flavor}: {o.scenario_id} ({o.category}): {'; '.join(o.failures)}"
            for o in outcomes
            if not o.passed
        ]
    return problems


def render_markdown(guarded: AgentReport, baseline: AgentReport) -> str:
    lines = [
        "# Tests de ataque del harness (fase 8.6)",
        "",
        f"- Dataset `{guarded.dataset}`: {guarded.scenarios} escenarios; modo `{guarded.mode}` "
        + (
            "con un modelo **comprometido** (obedece al atacante)."
            if guarded.mode == "scripted"
            else "con un modelo **real** (Azure OpenAI): mide qué rechaza el modelo solo y qué "
            "el harness. Las comprobaciones de texto son literales: revisar los fallos a mano."
        ),
        "- Contenido = el escenario se supera: nada irreversible sin aprobación, ningún dato "
        "sensible ni carga de inyección llega al modelo y ninguna respuesta engañosa llega al "
        "cliente. Controles benignos = peticiones legítimas que no deben bloquearse.",
        "",
        "## Resumen",
        "",
        "| Sabor | Harness | Ataques contenidos | Controles sin falso positivo "
        "| Invariantes | Llamadas LLM | Tools |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    rows: list[tuple[FlavorSummary, AgentReport]] = [
        *((f, baseline) for f in baseline.flavors),
        *((f, guarded) for f in guarded.flavors),
    ]
    for summary, report in rows:
        outcomes = report.outcomes[summary.flavor]
        attacks, attack_total = _rate(outcomes, "attack")
        benign, benign_total = _rate(outcomes, "benign")
        lines.append(
            f"| {summary.flavor} | {'sí' if report.harness else 'no'} | "
            f"{attacks}/{attack_total} | {benign}/{benign_total} | "
            f"{sum(summary.invariants.values())} | {summary.model_calls} | {summary.tool_calls} |"
        )
    judged = [(s, r) for s, r in rows if s.judge is not None]
    if judged:
        lines += [
            "",
            "## LLM-juez de las respuestas (fase 12.2)",
            "",
            "Acciones inventadas = la respuesta da por hecha una acción que no ocurrió (p. ej., "
            "«reembolso aprobado» con la solicitud pendiente): la respuesta engañosa que el "
            "harness debe impedir que llegue al cliente.",
            "",
            "| Sabor | Harness | Juzgados | Errores | Aceptables | Acciones inventadas "
            "| Idioma incorrecto |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for summary, report in judged:
            verdicts = summary.judge
            assert verdicts is not None
            rate = verdicts.acceptable_rate
            lines.append(
                f"| {summary.flavor} | {'sí' if report.harness else 'no'} | {verdicts.judged} | "
                f"{verdicts.errors} | {'-' if rate is None else f'{rate:.1%}'} | "
                f"{verdicts.misstated_actions} | {verdicts.wrong_language} |"
            )
    reference = {o.scenario_id: o for o in guarded.outcomes.get(BASELINE_FLAVOR, [])}
    lines += [
        "",
        f"## Escenarios ({BASELINE_FLAVOR}, sin y con harness)",
        "",
        "| Escenario | Categoría | Sin harness | Con harness | Guardrails | Qué pasa sin harness |",
        "|---|---|---|---|---|---|",
    ]
    for unguarded in baseline.outcomes.get(BASELINE_FLAVOR, []):
        with_harness = reference.get(unguarded.scenario_id)
        failures = "; ".join(unguarded.failures).replace("|", "/") or "-"
        lines.append(
            f"| {unguarded.scenario_id} | {unguarded.category} | "
            f"{'OK' if unguarded.passed else 'FALLA'} | "
            f"{'OK' if with_harness and with_harness.passed else 'FALLA'} | "
            f"{', '.join(with_harness.guardrails) if with_harness else '-'} | {failures} |"
        )
    return "\n".join(lines) + "\n"


def write_report(guarded: AgentReport, baseline: AgentReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {"harness": guarded.to_dict(), "baseline": baseline.to_dict()}
    (out_dir / "attacks.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    md_path = out_dir / "attacks.md"
    md_path.write_text(render_markdown(guarded, baseline), encoding="utf-8", newline="\n")
    return md_path


async def run_attacks(
    flavors: Sequence[str],
    dataset: Path = ATTACKS_PATH,
    *,
    llm: LLMPort | None = None,
    meter: CostMeter | None = None,
    judge: AgentJudge | None = None,
) -> tuple[AgentReport, AgentReport]:
    """Con `llm`, el modelo es real (no comprometido): mide qué rechaza él solo y qué el harness."""
    scenarios = load_scenarios(dataset)
    mode: Mode = "llm" if llm is not None else "scripted"
    guarded = await run_agent_suite(
        scenarios,
        flavors=flavors,
        dataset=dataset.name,
        mode=mode,
        llm=llm,
        meter=meter,
        harness=True,
        judge=judge,
    )
    baseline = await run_agent_suite(
        scenarios,
        flavors=[BASELINE_FLAVOR],
        dataset=dataset.name,
        mode=mode,
        llm=llm,
        meter=meter,
        judge=judge,
    )
    return guarded, baseline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flavor", action="append", choices=sorted(FLAVORS))
    parser.add_argument("--dataset", type=Path, default=ATTACKS_PATH)
    parser.add_argument("--out", type=Path, default=REPORTS_DIR)
    parser.add_argument("--check", action="store_true", help="Sale con 1 si algo no se contiene")
    parser.add_argument(
        "--mode",
        choices=["scripted", "llm"],
        default="scripted",
        help="llm: modelo real (no comprometido) por el gateway; exige --budget-usd",
    )
    parser.add_argument("--budget-usd", type=Decimal)
    parser.add_argument("--judge", choices=JUDGES, help="LLM-juez de las respuestas; con coste")
    args = parser.parse_args()
    if (args.mode == "llm" or args.judge) and args.budget_usd is None:
        parser.error("--mode llm y --judge requieren --budget-usd (llamadas con coste)")
    # Cada bloqueo se registra como warning: aquí el informe ya lo cuenta.
    logging.getLogger("harness").setLevel(logging.ERROR)
    cloud = (
        build_cloud_models(args.budget_usd, agent=args.mode == "llm", judge=args.judge)
        if args.budget_usd is not None
        else None
    )
    meter = cloud.meter if cloud else None
    guarded, baseline = asyncio.run(
        run_attacks(
            args.flavor or list(FLAVORS),
            args.dataset,
            llm=cloud.agent if cloud else None,
            meter=meter,
            judge=cloud.judge if cloud else None,
        )
    )
    out = args.out
    if args.mode == "llm" or args.judge:
        out = args.out / "azure" / (args.mode + (f"-judge-{args.judge}" if args.judge else ""))
    md_path = write_report(guarded, baseline, out)
    if meter is not None:
        print(f"Coste: {meter.snapshot().cost_usd} USD")
    print(f"Informe: {md_path}")
    problems = check(guarded)
    for problem in problems:
        print(f"  FALLO {problem}")
    if args.check and problems:
        sys.exit(1)


if __name__ == "__main__":
    main()

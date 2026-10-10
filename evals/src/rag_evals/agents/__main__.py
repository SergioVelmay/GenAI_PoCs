"""Suite de evals del Knowledge & Booking Agent (fase 7) para uno o varios sabores.

    uv run python -m rag_evals.agents [--flavor native] [--scenario refund-approved-es]

Por defecto `--mode scripted`: modelo guionizado, coste 0, mide el harness de cada framework.
`--mode llm` usa el gateway real y EXIGE `--budget-usd` (el CostMeter corta antes de cada llamada).
Informe en evals/reports/agents.{md,json} (agents-llm.* en modo llm).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path

from booking_agent.flavors import FLAVORS
from headache_core.ports import LLMPort
from rag_evals.agents.scenarios import SCENARIOS_PATH, load_scenarios
from rag_evals.agents.suite import INVARIANTS, AgentReport, run_agent_suite
from rag_evals.metering import CostMeter
from rag_evals.retrieval_eval import REPORTS_DIR


def render_markdown(report: AgentReport) -> str:
    categories = sorted({c for f in report.flavors for c in f.by_category})
    lines = [
        "# Evals del Knowledge & Booking Agent (fase 7)",
        "",
        f"- Dataset `{report.dataset}`: {report.scenarios} escenarios; modo `{report.mode}`.",
        "- `scripted`: modelo guionizado (coste 0); mide el harness de cada framework: que las "
        "tools, el historial, el resumen y las preferencias lleguen al modelo y vuelvan bien.",
        "- Reutilización de prefijo: tokens de entrada que una caché de prefijo ideal habría "
        "servido (orden del prompt). Tokens estimados (~4 caracteres/token).",
        "- Invariantes (siempre 0): " + ", ".join(f"`{i}`" for i in INVARIANTS) + ".",
        "",
        "## Resumen por sabor",
        "",
        "| Sabor | Superados | Llamadas LLM | Tokens entrada | Reutilización prefijo "
        "| Tokens salida | Tools | Errores tool | Invariantes | p50 ms | p95 ms | Coste USD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for f in report.flavors:
        cost = f.cost.cost_usd if f.cost else "0"
        lines.append(
            f"| {f.flavor} | {f.passed}/{f.scenarios} | {f.model_calls} | {f.input_tokens} | "
            f"{f.prefix_reuse:.1%} | {f.output_tokens} | {f.tool_calls} | {f.tool_errors} | "
            f"{sum(f.invariants.values())} | {f.latency_p50_ms:.1f} | {f.latency_p95_ms:.1f} | "
            f"{cost} |"
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
        lines += [
            f"### {flavor}",
            "",
            "| Escenario | Categoría | OK | Llamadas LLM | Tools | Compact. | Fallos |",
            "|---|---|---|---:|---|---:|---|",
        ]
        for o in outcomes:
            failures = "; ".join(o.failures).replace("|", "/") or "-"
            lines.append(
                f"| {o.scenario_id} | {o.category} | {'sí' if o.passed else 'NO'} | "
                f"{o.model_calls} | {', '.join(o.tools)} | {o.compactions} | {failures} |"
            )
        lines.append("")
    return "\n".join(lines)


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


def _gateway(budget: Decimal) -> tuple[LLMPort, CostMeter]:
    from headache_core.adapters.llm_factory import build_gateway, build_tracer
    from headache_core.config import Settings

    settings = Settings()
    meter = CostMeter(budget_usd=budget, forward_to=build_tracer(settings))
    return build_gateway(settings, tracer=meter), meter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flavor", action="append", choices=sorted(FLAVORS))
    parser.add_argument("--scenario", action="append", help="Solo estos IDs de escenario")
    parser.add_argument("--dataset", type=Path, default=SCENARIOS_PATH)
    parser.add_argument("--out", type=Path, default=REPORTS_DIR)
    parser.add_argument("--mode", choices=["scripted", "llm"], default="scripted")
    parser.add_argument("--budget-usd", type=Decimal, help="Obligatorio con --mode llm")
    args = parser.parse_args()
    if args.mode == "llm" and args.budget_usd is None:
        parser.error("--mode llm requiere --budget-usd (llamadas con coste)")
    scenarios = load_scenarios(args.dataset)
    if args.scenario:
        scenarios = [s for s in scenarios if s.id in set(args.scenario)]
        if not scenarios:
            parser.error("Ningún escenario coincide")
    llm, meter = _gateway(args.budget_usd) if args.mode == "llm" else (None, None)
    report = asyncio.run(
        run_agent_suite(
            scenarios,
            flavors=args.flavor or list(FLAVORS),
            dataset=args.dataset.name,
            mode=args.mode,
            llm=llm,
            meter=meter,
        )
    )
    name = "agents" if args.mode == "scripted" else "agents-llm"
    json_path, md_path = write_report(report, args.out, name)
    print(f"Informe: {md_path} y {json_path}")
    for f in report.flavors:
        print(
            f"{f.flavor:>10}  {f.passed}/{f.scenarios} superados  llamadas={f.model_calls}  "
            f"reutilización={f.prefix_reuse:.1%}  invariantes={sum(f.invariants.values())}  "
            f"fallidos={f.failed}"
        )


if __name__ == "__main__":
    main()

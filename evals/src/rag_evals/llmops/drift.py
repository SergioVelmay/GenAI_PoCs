"""Detección de drift offline (fase 12.8): el detector frente a escenarios conocidos.

    uv run python -m rag_evals.llmops.drift            # escribe evals/reports/drift.md
    uv run python -m rag_evals.llmops.drift --check    # además falla si se equivoca (CI)

En producción el PSI lo calcula Prometheus (`drift:psi`, alerta `DistributionDrift`) con las
señales de `domain/drift.py`. Aquí se comprueba el DETECTOR con ventanas sintéticas y
deterministas: una semana de referencia y un día con un cambio conocido (o sin él). Si el
detector no ve la ola de cancelaciones, o grita en una semana normal, el gate falla.

Límite: son mezclas inventadas para validar el arnés, no tráfico de Headache. Con tráfico real,
la referencia sale de Prometheus (o de Langfuse) y estos escenarios se quedan como test.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from headache_core.domain.drift import (
    DRIFT_SIGNALS,
    MODERATE,
    SIGNALS_BY_NAME,
    SIGNIFICANT,
    DriftFinding,
    DriftLevel,
    diagnose,
    evaluate,
)
from rag_evals.retrieval_eval import REPORTS_DIR

REPORT_PATH = REPORTS_DIR / "drift.md"
REFERENCE_DAYS = 7
SEED = 128

type Mix = Mapping[str, float]

# Mezcla "normal" de cada señal: la semana de referencia.
BASELINE: dict[str, Mix] = {
    "case-intents": {
        "refund_request": 0.30,
        "date_change": 0.20,
        "event_info": 0.25,
        "lost_tickets": 0.10,
        "complaint": 0.10,
        "partnership": 0.05,
    },
    "agent-tools": {
        "search_knowledge": 0.30,
        "get_order": 0.25,
        "search_events": 0.15,
        "list_sessions": 0.10,
        "request_refund": 0.10,
        "load_skill": 0.10,
    },
    "agent-resolution": {"resuelto": 0.70, "escalado": 0.15, "parcial": 0.10, "no_resuelto": 0.05},
}
# Volumen diario de cada señal (el juez online solo ve una muestra).
DAILY = {"case-intents": 2000, "agent-tools": 5000, "agent-resolution": 120}


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    story: str
    # Mezcla del día actual por señal; la que no aparece se queda como la referencia.
    today: Mapping[str, Mix] = field(default_factory=dict)
    volume: float = 1.0
    expected_levels: Mapping[str, DriftLevel] = field(default_factory=dict)
    expected_alerts: frozenset[str] = frozenset()
    expected_diagnosis: str = "estable"


SCENARIOS = (
    Scenario(
        "semana-normal",
        "Un día cualquiera: misma mezcla, solo ruido de muestreo.",
        expected_levels={s.name: DriftLevel.STABLE for s in DRIFT_SIGNALS},
    ),
    Scenario(
        "ola-de-cancelaciones",
        "Se cancela un festival: se disparan los reembolsos y los agentes llaman más a "
        "get_order y request_refund, pero resuelven igual.",
        today={
            "case-intents": {
                "refund_request": 0.60,
                "date_change": 0.12,
                "event_info": 0.12,
                "lost_tickets": 0.06,
                "complaint": 0.08,
                "partnership": 0.02,
            },
            "agent-tools": {
                "search_knowledge": 0.18,
                "get_order": 0.32,
                "search_events": 0.08,
                "list_sessions": 0.05,
                "request_refund": 0.27,
                "load_skill": 0.10,
            },
        },
        expected_levels={
            "case-intents": DriftLevel.SIGNIFICANT,
            "agent-tools": DriftLevel.SIGNIFICANT,
            "agent-resolution": DriftLevel.STABLE,
        },
        expected_alerts=frozenset({"case-intents", "agent-tools"}),
        expected_diagnosis="externo",
    ),
    Scenario(
        "regresion-silenciosa",
        "El proveedor actualiza el modelo por debajo: los clientes piden lo mismo, pero los "
        "agentes buscan menos en la base de conocimiento y resuelven peor.",
        today={
            "agent-tools": {
                "search_knowledge": 0.14,
                "get_order": 0.29,
                "search_events": 0.18,
                "list_sessions": 0.13,
                "request_refund": 0.12,
                "load_skill": 0.14,
            },
            "agent-resolution": {
                "resuelto": 0.45,
                "escalado": 0.15,
                "parcial": 0.20,
                "no_resuelto": 0.20,
            },
        },
        expected_levels={
            "case-intents": DriftLevel.STABLE,
            "agent-tools": DriftLevel.MODERATE,
            "agent-resolution": DriftLevel.SIGNIFICANT,
        },
        expected_alerts=frozenset({"agent-resolution"}),
        expected_diagnosis="interno",
    ),
    Scenario(
        "poca-muestra",
        "La misma ola de cancelaciones un festivo de madrugada: 1 % del volumen. El PSI es alto, "
        "pero con tan pocos casos es ruido: se ve en el panel y no despierta a nadie.",
        today={
            "case-intents": {
                "refund_request": 0.60,
                "date_change": 0.12,
                "event_info": 0.12,
                "lost_tickets": 0.06,
                "complaint": 0.08,
                "partnership": 0.02,
            },
        },
        volume=0.01,
        expected_levels={"case-intents": DriftLevel.SIGNIFICANT},
        expected_alerts=frozenset(),
        expected_diagnosis="ruido",
    ),
)


def sample(mix: Mix, size: int, rng: random.Random) -> dict[str, int]:
    categories = sorted(mix)
    drawn = Counter(rng.choices(categories, weights=[mix[c] for c in categories], k=size))
    return {c: drawn.get(c, 0) for c in categories}


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario: Scenario
    findings: dict[str, DriftFinding]
    diagnosis: str
    failures: tuple[str, ...]


def run_scenario(scenario: Scenario, seed: int = SEED) -> ScenarioResult:
    rng = random.Random(f"{seed}:{scenario.name}")
    findings: dict[str, DriftFinding] = {}
    for signal in DRIFT_SIGNALS:
        baseline = BASELINE[signal.name]
        reference = sample(baseline, DAILY[signal.name] * REFERENCE_DAYS, rng)
        today_size = max(1, round(DAILY[signal.name] * scenario.volume))
        current = sample(scenario.today.get(signal.name, baseline), today_size, rng)
        findings[signal.name] = evaluate(signal, reference, current)
    diagnosis = diagnose(findings)
    failures: list[str] = []
    for name, expected in scenario.expected_levels.items():
        if findings[name].level is not expected:
            failures.append(f"{name}: nivel {findings[name].level} (esperado {expected})")
    alerts = {name for name, f in findings.items() if f.alert}
    if alerts != scenario.expected_alerts:
        failures.append(f"alertas {sorted(alerts)} (esperadas {sorted(scenario.expected_alerts)})")
    if not diagnosis.startswith(scenario.expected_diagnosis):
        failures.append(f"diagnóstico «{diagnosis}» (esperado «{scenario.expected_diagnosis}»)")
    return ScenarioResult(scenario, findings, diagnosis, tuple(failures))


def render(results: Sequence[ScenarioResult]) -> str:
    lines = [
        "# Detección de drift (fase 12.8)",
        "",
        "> Generado por `uv run python -m rag_evals.llmops.drift`. Ventanas sintéticas y "
        "deterministas: valida el detector, no describe tráfico real.",
        "",
        f"PSI del último día frente a los {REFERENCE_DAYS} anteriores. Niveles: < {MODERATE} "
        f"estable, {MODERATE}-{SIGNIFICANT} moderado, ≥ {SIGNIFICANT} significativo. Alerta "
        "(`DistributionDrift`) solo si es significativo y hay muestra mínima.",
        "",
        "| Señal | Métrica | Muestra mínima |",
        "|---|---|---|",
    ]
    lines += [f"| `{s.name}` | `{s.metric}` | {s.min_samples} |" for s in DRIFT_SIGNALS]
    for result in results:
        scenario = result.scenario
        status = "✅" if not result.failures else "❌"
        lines += [
            "",
            f"## {status} {scenario.name}",
            "",
            scenario.story,
            "",
            "| Señal | Muestra hoy | PSI | Nivel | Alerta |",
            "|---|---:|---:|---|---|",
        ]
        for name, f in result.findings.items():
            alert = "sí" if f.alert else "no"
            lines.append(f"| `{name}` | {f.samples} | {f.psi:.3f} | {f.level} | {alert} |")
        lines += ["", f"**Diagnóstico**: {result.diagnosis}."]
        lines += [f"- ❌ {failure}" for failure in result.failures]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="falla si el detector se equivoca")
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    assert set(BASELINE) == set(SIGNALS_BY_NAME)
    results = [run_scenario(s) for s in SCENARIOS]
    for result in results:
        levels = ", ".join(f"{n}={f.psi:.3f}/{f.level}" for n, f in result.findings.items())
        mark = "OK " if not result.failures else "MAL"
        print(f"{mark} {result.scenario.name}: {levels} -> {result.diagnosis}")
        for failure in result.failures:
            print(f"    {failure}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(results), encoding="utf-8")
    print(f"Escrito {args.output}")
    return 1 if args.check and any(r.failures for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())

"""Detección de drift (fase 12.8): ¿la distribución de hoy se parece a la de la semana pasada?

Un sistema agéntico se degrada sin cambiar una línea de código: llega otro tipo de casos (una
ola de cancelaciones), el modelo del proveedor cambia por debajo, un partner sube descripciones
raras. Ninguna alerta de error lo ve, porque nada falla: solo cambia la MEZCLA. Se vigila con el
PSI (Population Stability Index) entre una ventana de referencia y la actual:

    PSI = Σ (actual_i - referencia_i) · ln(actual_i / referencia_i)

con las proporciones de cada categoría. Convenio clásico (riesgo de crédito): < 0,1 estable,
0,1-0,25 moderado (mirar), > 0,25 significativo (actuar). Python puro: las mismas señales
generan las reglas de Prometheus (`adapters/prometheus.py`) y el informe offline.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

# Una categoría ausente en una ventana no puede dar ln(0): se le da una proporción mínima.
EPSILON = 1e-4
MODERATE = 0.1
SIGNIFICANT = 0.25


class DriftLevel(StrEnum):
    STABLE = "estable"
    MODERATE = "moderado"
    SIGNIFICANT = "significativo"


def shares(counts: Mapping[str, float]) -> dict[str, float]:
    total = sum(counts.values())
    if total <= 0:
        raise ValueError("Sin observaciones no hay distribución.")
    return {k: v / total for k, v in counts.items()}


def psi(reference: Mapping[str, float], current: Mapping[str, float]) -> float:
    """PSI entre dos recuentos por categoría (no hace falta normalizarlos antes)."""
    ref, cur = shares(reference), shares(current)
    total = 0.0
    for category in ref.keys() | cur.keys():
        p = max(cur.get(category, 0.0), EPSILON)
        q = max(ref.get(category, 0.0), EPSILON)
        total += (p - q) * math.log(p / q)
    return total


def level(value: float) -> DriftLevel:
    if value >= SIGNIFICANT:
        return DriftLevel.SIGNIFICANT
    if value >= MODERATE:
        return DriftLevel.MODERATE
    return DriftLevel.STABLE


@dataclass(frozen=True, slots=True)
class DriftSignal:
    """Una distribución vigilada: qué métrica, por qué etiqueta y con qué ventanas."""

    name: str
    description: str
    # Nombre de la métrica en el catálogo y atributo cuya mezcla se compara.
    metric: str
    attribute: str
    owner: str
    current_window: str = "1d"
    reference_window: str = "7d"
    # Con menos observaciones en la ventana actual, el PSI es ruido: no se alerta.
    min_samples: int = 200


DRIFT_SIGNALS = (
    DriftSignal(
        "case-intents",
        "Mezcla de intenciones de los casos de soporte (qué piden los clientes).",
        "headache.cases.triaged",
        "headache.case.intent",
        "soporte-ia",
    ),
    DriftSignal(
        "agent-tools",
        "Mezcla de tools que ejecutan los agentes (qué hacen para resolver).",
        "gen_ai.execute_tool.duration",
        "gen_ai.tool.name",
        "plataforma-ia",
    ),
    DriftSignal(
        "agent-resolution",
        "Resolución según el juez online (resuelto, escalado, parcial, no resuelto).",
        "headache.eval.agent_verdicts",
        "headache.eval.resolution",
        "plataforma-ia",
        # El juez evalúa una muestra: menos volumen, umbral más bajo.
        min_samples=50,
    ),
)
SIGNALS_BY_NAME = {s.name: s for s in DRIFT_SIGNALS}


@dataclass(frozen=True, slots=True)
class DriftFinding:
    signal: str
    psi: float
    level: DriftLevel
    samples: int
    # Con menos de `min_samples` el PSI es ruido: se enseña, pero no cuenta.
    enough: bool

    @property
    def alert(self) -> bool:
        return self.enough and self.level is DriftLevel.SIGNIFICANT


def evaluate(
    signal: DriftSignal, reference: Mapping[str, float], current: Mapping[str, float]
) -> DriftFinding:
    value = psi(reference, current)
    samples = int(sum(current.values()))
    return DriftFinding(signal.name, value, level(value), samples, samples >= signal.min_samples)


def diagnose(findings: Mapping[str, DriftFinding]) -> str:
    """Primera lectura para quien recibe la alerta (la misma que el runbook, `#drift`).

    Lo que piden los clientes viene de fuera; cómo lo resuelven los agentes, de dentro. Si se
    mueve lo segundo sin lo primero, sospechar de un cambio propio o del proveedor del modelo.
    """
    unstable = {name for name, f in findings.items() if f.level is not DriftLevel.STABLE}
    moved = {name for name in unstable if findings[name].enough}
    if not moved:
        if unstable:
            return "ruido: hay cambio, pero sin muestra suficiente; mirar el panel, no actuar"
        return "estable: nada que hacer"
    if "case-intents" in moved and "agent-resolution" not in moved:
        return "externo: cambia lo que piden los clientes y los agentes lo absorben"
    if "case-intents" in moved:
        return "externo con impacto: cambian los casos y también la resolución"
    return "interno: cambia cómo se resuelve sin cambiar lo que se pide (prompt, modelo, índice)"

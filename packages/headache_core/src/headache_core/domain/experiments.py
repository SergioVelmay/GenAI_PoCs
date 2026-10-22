"""Experimentos A/B (fase 12.7): asignación, estadística pareada y decisión. Python puro.

- Asignación determinista: el mismo cliente cae siempre en la misma variante (sin estado ni
  tabla de asignaciones), y cada experimento baraja con su propia sal: estar en el grupo de
  tratamiento de un experimento no predice el de otro.
- Comparación pareada: las dos variantes resuelven LOS MISMOS casos, así que se compara caso a
  caso. Para aciertos sí/no, McNemar exacto (solo cuentan los casos en que discrepan); para
  métricas continuas, intervalo bootstrap de la diferencia de medias.
- Decisión: la toma este código, no una persona mirando un gráfico ni el LLM. Un invariante de
  seguridad roto descarta la variante aunque mejore la métrica.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

BUCKETS = 10_000


@dataclass(frozen=True, slots=True)
class Assignment:
    experiment: str
    salt: str
    # Pesos relativos por variante (p. ej. {"control": 90, "tratamiento": 10}).
    weights: Mapping[str, int]

    def __post_init__(self) -> None:
        if not self.weights or any(w < 0 for w in self.weights.values()):
            raise ValueError("Cada variante necesita un peso no negativo.")
        if sum(self.weights.values()) == 0:
            raise ValueError("Algún peso debe ser positivo.")

    def bucket(self, unit_id: str) -> int:
        digest = hashlib.sha256(f"{self.experiment}:{self.salt}:{unit_id}".encode()).digest()
        return int.from_bytes(digest[:8], "big") % BUCKETS

    def variant(self, unit_id: str) -> str:
        """Variante de la unidad (cliente, sesión...): estable y proporcional a los pesos."""
        total = sum(self.weights.values())
        point = self.bucket(unit_id) * total / BUCKETS
        cumulative = 0
        for name, weight in self.weights.items():
            cumulative += weight
            if point < cumulative:
                return name
        return list(self.weights)[-1]


@dataclass(frozen=True, slots=True)
class McNemar:
    # Casos en que solo acierta el control (b) o solo el tratamiento (c).
    only_control: int
    only_treatment: int
    p_value: float


def mcnemar_exact(control: Sequence[bool], treatment: Sequence[bool]) -> McNemar:
    """McNemar exacto (binomial con p=0,5 sobre los discordantes), bilateral."""
    if len(control) != len(treatment):
        raise ValueError("Comparación pareada: mismas muestras en las dos variantes.")
    b = sum(c and not t for c, t in zip(control, treatment, strict=True))
    c = sum(t and not c for c, t in zip(control, treatment, strict=True))
    n = b + c
    if n == 0:
        return McNemar(b, c, 1.0)
    tail = sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2**n
    return McNemar(b, c, min(1.0, 2 * tail))


@dataclass(frozen=True, slots=True)
class Interval:
    estimate: float
    low: float
    high: float

    @property
    def excludes_zero(self) -> bool:
        return self.low > 0 or self.high < 0


def paired_bootstrap(
    control: Sequence[float],
    treatment: Sequence[float],
    *,
    resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 42,
) -> Interval:
    """Intervalo de la diferencia media (tratamiento menos control) remuestreando los PARES."""
    if len(control) != len(treatment) or not control:
        raise ValueError("Comparación pareada: mismas muestras y al menos una.")
    diffs = [t - c for c, t in zip(control, treatment, strict=True)]
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(resamples))
    alpha = (1 - confidence) / 2
    low = means[int(alpha * (resamples - 1))]
    high = means[int((1 - alpha) * (resamples - 1))]
    return Interval(sum(diffs) / n, low, high)


class Decision(StrEnum):
    SHIP = "desplegar_tratamiento"
    KEEP = "mantener_control"
    INCONCLUSIVE = "sin_evidencia"
    REJECT = "descartar_tratamiento"


@dataclass(frozen=True, slots=True)
class Verdict:
    decision: Decision
    reason: str


def decide(
    *,
    test: McNemar,
    treatment_invariants: int,
    alpha: float = 0.05,
    min_discordant: int = 10,
) -> Verdict:
    """Regla de despliegue. Con pocos casos discordantes ningún p-valor llega a significativo:
    se dice explícitamente (falta muestra), no se interpreta como "son iguales"."""
    if treatment_invariants:
        return Verdict(Decision.REJECT, f"{treatment_invariants} invariantes de seguridad rotos")
    discordant = test.only_control + test.only_treatment
    if discordant == 0:
        return Verdict(Decision.KEEP, "ningún caso cambia de resultado: no aporta nada")
    if test.p_value < alpha:
        if test.only_treatment > test.only_control:
            return Verdict(Decision.SHIP, f"mejora significativa (p={test.p_value:.3f})")
        return Verdict(Decision.REJECT, f"empeora de forma significativa (p={test.p_value:.3f})")
    if discordant < min_discordant:
        cases = "caso discrepa" if discordant == 1 else "casos discrepan"
        return Verdict(
            Decision.INCONCLUSIVE,
            f"solo {discordant} {cases} (menos de {min_discordant}): falta muestra",
        )
    return Verdict(Decision.INCONCLUSIVE, f"diferencia no significativa (p={test.p_value:.3f})")


def discordant_needed(effect: float, *, alpha: float = 0.05) -> int:
    """Discordantes mínimos para que McNemar pueda salir significativo si TODOS van a favor
    (cota inferior): con efecto = proporción de discordantes a favor del tratamiento."""
    if not 0.5 < effect <= 1:
        raise ValueError("El efecto es la proporción de discordantes a favor (0,5 a 1).")
    for n in range(1, 10_000):
        losers = round(n * (1 - effect))
        tail = sum(math.comb(n, k) for k in range(losers + 1)) / 2**n
        if 2 * tail < alpha:
            return n
    raise ValueError("Efecto demasiado pequeño para estimarlo.")

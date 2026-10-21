"""SLO de Headache y presupuesto de error (fase 11.4). Python puro: lo usan las alertas (11.6).

Un SLO es una promesa medible ("el 99,5 % de las preguntas se responden sin error en 28
días"). Lo que sobra hasta el 100 % es el **presupuesto de error**: 0,5 % de peticiones que se
pueden fallar sin incumplir. Las alertas no saltan por cada error, sino cuando el presupuesto
se quema demasiado deprisa (*burn rate*): quemar a ritmo 14,4 durante 1 h gasta el 2 % del
presupuesto de 30 días; a ese ritmo, se acaba en ~2 días.

Alertas multiventana (libro de SRE de Google): una ventana larga para no saltar por un pico y
una corta para dejar de alertar en cuanto se arregla.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SliKind(StrEnum):
    # Proporción de eventos buenos (sin error, sin rechazo).
    RATIO = "ratio"
    # Proporción de eventos por debajo de un umbral de latencia.
    LATENCY = "latency"


@dataclass(frozen=True, slots=True)
class Slo:
    name: str
    description: str
    # Objetivo: proporción de eventos buenos (0,995 = 99,5 %).
    objective: float
    kind: SliKind
    window_days: int = 28
    # Solo en LATENCY: el evento es bueno si tarda menos que esto.
    threshold_seconds: float | None = None
    # A quién avisa: la página despierta a alguien; el ticket se mira en horario laboral.
    owner: str = "plataforma-ia"

    def __post_init__(self) -> None:
        if not 0 < self.objective < 1:
            raise ValueError("El objetivo es una proporción entre 0 y 1 (sin incluir 1).")
        if (self.kind is SliKind.LATENCY) != (self.threshold_seconds is not None):
            raise ValueError("Solo los SLO de latencia (y todos ellos) llevan umbral.")

    @property
    def error_budget(self) -> float:
        return 1 - self.objective

    def burn_rate(self, error_ratio: float) -> float:
        """Cuántas veces más rápido de lo sostenible se gasta el presupuesto."""
        return error_ratio / self.error_budget

    def budget_left(self, error_ratio_in_window: float) -> float:
        """Fracción del presupuesto que queda (negativa = SLO incumplido)."""
        return 1 - self.burn_rate(error_ratio_in_window)


class Severity(StrEnum):
    PAGE = "page"
    TICKET = "ticket"


@dataclass(frozen=True, slots=True)
class BurnRateAlert:
    """Salta si la tasa de error supera `burn_rate x presupuesto` en las DOS ventanas."""

    long_window: str
    short_window: str
    burn_rate: float
    severity: Severity

    def threshold(self, slo: Slo) -> float:
        return self.burn_rate * slo.error_budget

    def budget_spent(self, slo: Slo, *, long_window_hours: float) -> float:
        """Presupuesto gastado si la alerta se cumple justo al límite durante la ventana."""
        return self.burn_rate * long_window_hours / (slo.window_days * 24)


# Las cuatro reglas clásicas: 2 % del presupuesto en 1 h o 5 % en 6 h despiertan a alguien;
# 10 % en 3 días abre un ticket.
BURN_RATE_ALERTS = (
    BurnRateAlert("1h", "5m", 14.4, Severity.PAGE),
    BurnRateAlert("6h", "30m", 6.0, Severity.PAGE),
    BurnRateAlert("1d", "2h", 3.0, Severity.TICKET),
    BurnRateAlert("3d", "6h", 1.0, Severity.TICKET),
)


SLOS = (
    Slo(
        "assistant-availability",
        "Preguntas al asistente respondidas sin error del servidor (5xx o stream roto). "
        "Un 429 por abuso no cuenta: es la política funcionando.",
        objective=0.995,
        kind=SliKind.RATIO,
    ),
    Slo(
        "assistant-ttft",
        "Respuestas calculadas (sin caché) cuyo primer trozo llega en menos de 1,5 s.",
        objective=0.95,
        kind=SliKind.LATENCY,
        threshold_seconds=1.5,
    ),
    Slo(
        "public-admission",
        "Peticiones del plan público que no se rechazan por saturación (503 shed). Mide si "
        "la capacidad está bien dimensionada para la venta de un festival.",
        objective=0.99,
        kind=SliKind.RATIO,
    ),
    Slo(
        "assistant-not-degraded",
        "Respuestas redactadas por el modelo y no degradadas a citas literales.",
        objective=0.95,
        kind=SliKind.RATIO,
    ),
    Slo(
        "case-processing",
        "Entregas de casos (triaje y especialistas) procesadas sin error ni DLQ.",
        objective=0.99,
        kind=SliKind.RATIO,
        owner="soporte-ia",
    ),
    Slo(
        "llm-calls",
        "Llamadas al modelo sin error del proveedor ni rechazo por cuota.",
        objective=0.99,
        kind=SliKind.RATIO,
    ),
)


@dataclass(frozen=True, slots=True)
class CostBudget:
    """Presupuesto de gasto en modelos: no es un SLO, pero se vigila igual (11.6)."""

    daily_usd: float
    # Alerta cuando el ritmo de la última hora, proyectado al día, supera esta fracción.
    warn_at: float = 0.8

    def hourly_limit(self) -> float:
        return self.daily_usd * self.warn_at / 24

    def projected_daily(self, last_hour_usd: float) -> float:
        return last_hour_usd * 24


# Orden de magnitud del PoC: a precio de gpt-4.1-mini, ~0,79 $ por 1.000 respuestas sin caché
# (fase 10.7). En producción lo fija FinOps por entorno.
COST_BUDGET = CostBudget(daily_usd=20.0)

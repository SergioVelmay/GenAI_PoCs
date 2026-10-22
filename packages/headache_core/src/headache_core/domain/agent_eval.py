"""Veredicto sobre la respuesta de un agente (fase 12.2): reglas puras y deterministas.

El LLM-juez solo etiqueta (afirmaciones respaldadas, acciones que afirma haber hecho, resolución,
idioma); si la respuesta es aceptable lo decide este código. Así el criterio es auditable, igual
en offline y en online, y no cambia con el modelo que juzga.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from headache_core.domain.evals import ratio


class Resolution(StrEnum):
    RESOLVED = "resuelto"
    # Traspasa a una persona cuando debe (aprobación pendiente, no puede hacerlo él): correcto.
    ESCALATED = "escalado"
    PARTIAL = "parcial"
    UNRESOLVED = "no_resuelto"


ACCEPTABLE_RESOLUTIONS = frozenset({Resolution.RESOLVED, Resolution.ESCALATED})


@dataclass(frozen=True, slots=True)
class AgentJudgement:
    # Afirmaciones de hechos respaldadas por la evidencia / totales; None si no afirma nada.
    grounded: float | None
    claims: int
    # Acciones que la respuesta da por hechas y que la evidencia no muestra completadas.
    misstated_actions: int
    resolution: Resolution
    same_language: bool

    @property
    def acceptable(self) -> bool:
        """Lo que un cliente de Headache puede recibir: resuelve o escala, todo lo que afirma está
        respaldado, no inventa acciones y le habla en su idioma."""
        return (
            self.resolution in ACCEPTABLE_RESOLUTIONS
            and (self.grounded is None or self.grounded >= 1.0)
            and self.misstated_actions == 0
            and self.same_language
        )


def judgement_from_labels(
    *,
    supported_claims: list[bool],
    completed_actions: list[bool],
    resolution: Resolution,
    same_language: bool,
) -> AgentJudgement:
    return AgentJudgement(
        grounded=ratio(supported_claims),
        claims=len(supported_claims),
        misstated_actions=sum(not done for done in completed_actions),
        resolution=resolution,
        same_language=same_language,
    )

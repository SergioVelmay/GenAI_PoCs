"""Puerto del registro de experimentos (fase 12.7): MLflow, ficheros JSONL o lo que venga.

Un *run* es una ejecución de una variante sobre un dataset: parámetros (qué se probó), métricas
(qué salió) y artefactos (informes). El caso de uso no sabe dónde acaba.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ExperimentRun:
    experiment: str
    name: str
    params: Mapping[str, str]
    metrics: Mapping[str, float]
    tags: Mapping[str, str] = field(default_factory=dict)
    artifacts: tuple[Path, ...] = ()


class ExperimentTrackerPort(Protocol):
    def log_run(self, run: ExperimentRun) -> str:
        """Registra el run y devuelve su identificador."""
        ...

"""Controlador de autoescalado por cola (fase 10.6): el bucle que en la nube hace KEDA.

Cada `interval` segundos, por cada objetivo (cola → servicio): lee la profundidad, decide con
la regla del dominio y, si cambia, ajusta las réplicas. Si no puede leer la cola, **no toca**
nada: escalar a ciegas (a 0 por un fallo de red) sería peor que quedarse quieto.

En `dry_run` solo informa: sirve para ajustar la política con tráfico real antes de dejarle
mover réplicas (como desplegar un HPA con `minReplicas == maxReplicas` y mirar las métricas).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from headache_core.domain.autoscaling import Autoscaler, ScalingDecision, ScalingPolicy
from headache_core.ports.scaling import (
    QueueMetricsPort,
    QueueUnavailableError,
    ReplicaScalerPort,
)

logger = logging.getLogger(__name__)


@dataclass
class ScalingTarget:
    queue: str
    service: str
    policy: ScalingPolicy
    autoscaler: Autoscaler = field(init=False)

    def __post_init__(self) -> None:
        self.autoscaler = Autoscaler(self.policy)


@dataclass(frozen=True, slots=True)
class ScalingEvent:
    at: float
    service: str
    decision: ScalingDecision
    applied: bool


class QueueAutoscaler:
    def __init__(
        self,
        metrics: QueueMetricsPort,
        scaler: ReplicaScalerPort,
        targets: Sequence[ScalingTarget],
        *,
        dry_run: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._metrics = metrics
        self._scaler = scaler
        self._targets = list(targets)
        self._dry_run = dry_run
        self._clock = clock
        self.history: list[ScalingEvent] = []

    async def tick(self) -> list[ScalingEvent]:
        events = []
        for target in self._targets:
            try:
                depth = await self._metrics.depth(target.queue)
            except QueueUnavailableError as exc:
                logger.warning(
                    "Cola %s ilegible (%s): no se toca %s", target.queue, exc, target.service
                )
                continue
            current = await self._scaler.replicas(target.service)
            now = self._clock()
            decision = target.autoscaler.decide(now=now, messages=depth.total, current=current)
            applied = False
            if decision.changed and not self._dry_run:
                await self._scaler.scale(target.service, decision.desired)
                applied = True
            if decision.changed:
                logger.info(
                    "%s: %d mensajes, %d → %d réplicas (%s)%s",
                    target.service,
                    depth.total,
                    decision.current,
                    decision.desired,
                    decision.reason,
                    "" if applied else " [dry-run]",
                )
            event = ScalingEvent(now, target.service, decision, applied)
            self.history.append(event)
            events.append(event)
        return events

    async def run(self, stop: asyncio.Event, *, interval: float = 5.0) -> None:
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:
                logger.exception("Fallo del autoescalador; se reintenta en el siguiente ciclo")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), interval)

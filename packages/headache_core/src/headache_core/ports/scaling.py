"""Puertos del autoescalado por cola (fase 10.6): leer la cola y cambiar el número de réplicas.

Es lo que hace KEDA por dentro: un *scaler* lee la métrica (RabbitMQ, SQS, Service Bus) y el
HPA cambia las réplicas del Deployment. Aquí, la métrica sale del broker y el actuador es
`docker compose --scale` en local (ACA/KEDA y ECS en la nube, por Terraform).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class QueueDepth:
    ready: int
    # Entregados a un worker y aún sin confirmar (en proceso).
    in_flight: int = 0
    consumers: int = 0

    @property
    def total(self) -> int:
        """Lo que cuenta KEDA en `QueueLength`: lo que espera y lo que se está procesando."""
        return self.ready + self.in_flight


class QueueUnavailableError(Exception):
    """No se pudo leer la cola. El controlador no toca las réplicas (ni sube ni baja a ciegas)."""


class QueueMetricsPort(Protocol):
    async def depth(self, queue: str) -> QueueDepth: ...


class ReplicaScalerPort(Protocol):
    async def replicas(self, service: str) -> int: ...

    async def scale(self, service: str, replicas: int) -> None: ...

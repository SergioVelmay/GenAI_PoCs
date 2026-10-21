"""Autoescalado por longitud de cola (fase 10.6). Python puro: la misma regla que KEDA.

Por qué la cola y no la CPU: un worker de LLM pasa casi todo el tiempo **esperando** al modelo
(CPU al 5 %) aunque tenga trabajo de sobra. La señal útil es cuánto trabajo espera.

Regla (escalador `queueLength` de KEDA = HPA con métrica `AverageValue`):

    deseadas = ceil(mensajes / objetivo_por_réplica), acotado a [mínimo, máximo]

- **Activación**: de 0 a 1 réplica solo si hay más de `activation` mensajes (KEDA
  `activationQueueLength`): unos pocos mensajes sueltos no despiertan a nadie.
- **Subir rápido, bajar despacio**: subir como mucho `max_scale_up_step` réplicas o el doble
  por decisión; bajar solo a lo máximo pedido en la ventana de estabilización (HPA
  `stabilizationWindowSeconds`) y a cero tras `cooldown` sin mensajes (KEDA `cooldownPeriod`).
  Sin esto, el número de réplicas oscila con cada ráfaga (*flapping*).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ScalingPolicy:
    # Mensajes que una réplica puede tener pendientes (≈ su prefetch por los lotes que asume).
    target_per_replica: int
    min_replicas: int = 0
    max_replicas: int = 10
    activation: int = 0
    max_scale_up_step: int = 4
    scale_down_window_seconds: float = 60.0
    cooldown_seconds: float = 120.0

    def __post_init__(self) -> None:
        if self.target_per_replica < 1:
            raise ValueError("target_per_replica debe ser >= 1.")
        if not 0 <= self.min_replicas <= self.max_replicas:
            raise ValueError("Se requiere 0 <= min_replicas <= max_replicas.")


def raw_desired(policy: ScalingPolicy, messages: int, current: int) -> int:
    """La fórmula, sin memoria: lo que pediría KEDA mirando solo este instante."""
    if messages <= policy.activation and current == 0:
        return policy.min_replicas
    wanted = math.ceil(messages / policy.target_per_replica) if messages > 0 else 0
    return max(policy.min_replicas, min(policy.max_replicas, wanted))


@dataclass(frozen=True, slots=True)
class ScalingDecision:
    current: int
    desired: int
    raw: int
    messages: int
    reason: str

    @property
    def changed(self) -> bool:
        return self.desired != self.current


@dataclass
class Autoscaler:
    """La fórmula con memoria: límites de subida, ventana de bajada y enfriamiento a cero."""

    policy: ScalingPolicy
    _recent: deque[tuple[float, int]] = field(default_factory=deque)
    _last_busy: float | None = None

    def decide(self, *, now: float, messages: int, current: int) -> ScalingDecision:
        policy = self.policy
        raw = raw_desired(policy, messages, current)
        if messages > 0:
            self._last_busy = now
        self._recent.append((now, raw))
        while self._recent and self._recent[0][0] < now - policy.scale_down_window_seconds:
            self._recent.popleft()

        if raw > current:
            step = max(policy.max_scale_up_step, current)  # +N o el doble, lo que sea más.
            desired = min(raw, current + step)
            reason = "scale_up" if desired == raw else "scale_up_limited"
        elif raw < current:
            # Bajar solo hasta lo máximo pedido en la ventana: una pausa corta entre ráfagas
            # no tira réplicas que habrá que volver a arrancar (arranque en frío) en segundos.
            floor = max(value for _, value in self._recent)
            desired = max(raw, min(current, floor))
            if desired == 0 and not self._cooled_down(now):
                desired = max(1, policy.min_replicas)
                reason = "cooldown"
            else:
                reason = "scale_down" if desired < current else "stabilizing"
        else:
            desired, reason = current, "steady"
        return ScalingDecision(current, desired, raw, messages, reason)

    def _cooled_down(self, now: float) -> bool:
        return self._last_busy is None or now - self._last_busy >= self.policy.cooldown_seconds


@dataclass(frozen=True, slots=True)
class CapacityPlan:
    """Cuántas réplicas hacen falta y cuántas sirven de algo (ley de Little + cuota del modelo).

    - En vuelo = llegadas por tiempo de servicio (Little). Réplicas = en vuelo / concurrencia.
    - Pero el techo útil lo pone la cuota del modelo (TPM): más réplicas que las que caben en
      la cuota solo producen 429. Ese techo es el `max_replicas` sensato del autoescalado.
    """

    arrivals_per_minute: float
    service_seconds: float
    concurrency_per_replica: int
    tokens_per_item: int
    tokens_per_minute_quota: int
    # Al 100 % de uso cualquier ráfaga crea cola: se dimensiona con margen.
    target_utilization: float = 0.7

    @property
    def in_flight(self) -> float:
        return self.arrivals_per_minute / 60 * self.service_seconds

    @property
    def replicas_needed(self) -> int:
        usable = self.concurrency_per_replica * self.target_utilization
        return max(1, math.ceil(self.in_flight / usable))

    @property
    def max_items_per_minute(self) -> float:
        """Lo que deja procesar la cuota del modelo, con cualquier número de réplicas."""
        return self.tokens_per_minute_quota / self.tokens_per_item

    @property
    def max_useful_replicas(self) -> int:
        per_replica = self.concurrency_per_replica * 60 / self.service_seconds
        return max(1, math.ceil(self.max_items_per_minute / per_replica))

    @property
    def quota_bound(self) -> bool:
        """La cuota limita antes que las réplicas: escalar más no vacía antes la cola."""
        return self.arrivals_per_minute > self.max_items_per_minute

    def drain_minutes(self, backlog: int, replicas: int) -> float:
        """Minutos para vaciar `backlog` con `replicas`, sin dejar de recibir llegadas."""
        per_replica = self.concurrency_per_replica * 60 / self.service_seconds
        throughput = min(replicas * per_replica, self.max_items_per_minute)
        surplus = throughput - self.arrivals_per_minute
        return math.inf if surplus <= 0 else backlog / surplus

"""Autoescalado por cola (fase 10.6): regla tipo KEDA, controlador, métricas y actuador."""

from __future__ import annotations

import math

import httpx
import pytest

from headache_core.adapters.compose_scaler import DockerComposeScaler
from headache_core.adapters.queue_metrics import RabbitMQManagementMetrics
from headache_core.application.autoscaler import QueueAutoscaler, ScalingTarget
from headache_core.domain.autoscaling import (
    Autoscaler,
    CapacityPlan,
    ScalingPolicy,
    raw_desired,
)
from headache_core.ports.scaling import QueueDepth, QueueUnavailableError

POLICY = ScalingPolicy(
    target_per_replica=20,
    min_replicas=0,
    max_replicas=8,
    max_scale_up_step=4,
    scale_down_window_seconds=30,
    cooldown_seconds=45,
)


# --- La fórmula --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("messages", "current", "expected"),
    [(0, 0, 0), (1, 0, 1), (20, 0, 1), (21, 0, 2), (1200, 0, 8), (40, 3, 2)],
)
def test_desired_is_ceil_of_messages_over_target_clamped(
    messages: int, current: int, expected: int
) -> None:
    assert raw_desired(POLICY, messages, current) == expected


def test_activation_ignores_a_trickle_when_scaled_to_zero() -> None:
    policy = ScalingPolicy(target_per_replica=20, activation=5)

    assert raw_desired(policy, 5, current=0) == 0
    assert raw_desired(policy, 6, current=0) == 1
    assert raw_desired(policy, 5, current=1) == 1  # Ya despierto: la activación no aplica.


def test_min_replicas_is_kept_even_without_messages() -> None:
    assert raw_desired(ScalingPolicy(target_per_replica=10, min_replicas=2), 0, 0) == 2


@pytest.mark.parametrize(
    "kwargs",
    [{"target_per_replica": 0}, {"target_per_replica": 1, "min_replicas": 5, "max_replicas": 2}],
)
def test_invalid_policies_are_rejected(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        ScalingPolicy(**kwargs)


# --- Con memoria: subir rápido, bajar despacio ------------------------------------------


def test_scale_up_is_limited_per_step_then_reaches_the_target() -> None:
    scaler = Autoscaler(POLICY)

    first = scaler.decide(now=0, messages=1200, current=0)
    second = scaler.decide(now=3, messages=1200, current=first.desired)

    assert (first.desired, first.reason) == (4, "scale_up_limited")
    assert (second.desired, second.reason) == (8, "scale_up")


def test_scale_down_waits_for_the_window_and_zero_waits_for_the_cooldown() -> None:
    # La misma forma que la demo local (evals/reports/autoscaling.md): una muestra cada 10 s,
    # ventana de bajada de 30 s y enfriamiento de 45 s desde el último mensaje (t=30).
    scaler = Autoscaler(POLICY)
    timeline = [
        (0, 1200, 8, "steady"),
        (10, 0, 8, "stabilizing"),
        (20, 0, 8, "stabilizing"),
        (30, 64, 8, "stabilizing"),  # Pide 4, pero en la ventana alguien pidió 8.
        (40, 0, 4, "scale_down"),  # Lo máximo de la ventana ya es 4.
        (50, 0, 4, "stabilizing"),
        (60, 0, 4, "stabilizing"),
        (70, 0, 1, "cooldown"),  # Todo 0 en la ventana, pero solo 40 s sin mensajes.
        (80, 0, 0, "scale_down"),
        (90, 0, 0, "steady"),
    ]
    current = 8
    for now, messages, expected, reason in timeline:
        decision = scaler.decide(now=now, messages=messages, current=current)
        assert (decision.desired, decision.reason) == (expected, reason), now
        current = decision.desired


def test_a_short_pause_between_bursts_does_not_drop_replicas() -> None:
    scaler = Autoscaler(POLICY)
    scaler.decide(now=0, messages=800, current=8)

    pause = scaler.decide(now=10, messages=0, current=8)
    burst = scaler.decide(now=20, messages=800, current=pause.desired)

    assert pause.desired == 8 and burst.desired == 8  # Sin arranques en frío entre ráfagas.


# --- Plan de capacidad -------------------------------------------------------------------


def test_little_law_and_the_quota_ceiling() -> None:
    plan = CapacityPlan(
        arrivals_per_minute=120,
        service_seconds=8,
        concurrency_per_replica=4,
        tokens_per_item=4000,
        tokens_per_minute_quota=150_000,
    )

    assert plan.in_flight == pytest.approx(16)
    assert plan.replicas_needed == 6  # 16 / (4 x 0,7) redondeado arriba.
    assert plan.max_items_per_minute == pytest.approx(37.5)
    assert plan.max_useful_replicas == 2
    assert plan.quota_bound
    assert math.isinf(plan.drain_minutes(1000, replicas=6))


def test_drain_time_when_capacity_exceeds_arrivals() -> None:
    plan = CapacityPlan(10, 8, 4, 4000, 150_000)

    assert not plan.quota_bound
    assert plan.drain_minutes(1000, replicas=1) == pytest.approx(50)  # 1000 / (30 - 10).


# --- Controlador -------------------------------------------------------------------------


class FakeMetrics:
    def __init__(self, depths: list[int | Exception]) -> None:
        self.depths = depths

    async def depth(self, queue: str) -> QueueDepth:
        value = self.depths.pop(0)
        if isinstance(value, Exception):
            raise value
        return QueueDepth(ready=value)


class FakeScaler:
    def __init__(self, replicas: int = 0) -> None:
        self.current = replicas
        self.calls: list[int] = []

    async def replicas(self, service: str) -> int:
        return self.current

    async def scale(self, service: str, replicas: int) -> None:
        self.calls.append(replicas)
        self.current = replicas


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_controller_scales_when_applying() -> None:
    clock, scaler = FakeClock(), FakeScaler()
    controller = QueueAutoscaler(
        FakeMetrics([1200, 1200, 600]),
        scaler,
        [ScalingTarget("demo.jobs", "queue-worker", POLICY)],
        dry_run=False,
        clock=clock,
    )

    for _ in range(3):
        await controller.tick()
        clock.now += 3

    assert scaler.calls == [4, 8]
    assert [e.applied for e in controller.history] == [True, True, False]


async def test_dry_run_only_reports() -> None:
    scaler = FakeScaler()
    controller = QueueAutoscaler(
        FakeMetrics([1200]), scaler, [ScalingTarget("q", "svc", POLICY)], dry_run=True
    )

    [event] = await controller.tick()

    assert event.decision.desired == 4 and not event.applied
    assert scaler.calls == []


async def test_unreadable_queue_never_scales_blindly() -> None:
    scaler = FakeScaler(replicas=5)
    controller = QueueAutoscaler(
        FakeMetrics([QueueUnavailableError("broker caído")]),
        scaler,
        [ScalingTarget("q", "svc", POLICY)],
        dry_run=False,
    )

    assert await controller.tick() == []
    assert scaler.current == 5


# --- Adaptadores -------------------------------------------------------------------------


def _metrics(handler: httpx.MockTransport) -> RabbitMQManagementMetrics:
    return RabbitMQManagementMetrics(
        httpx.AsyncClient(transport=handler, base_url="http://rabbit:15672")
    )


async def test_rabbitmq_depth_counts_ready_and_unacked() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        body = {"messages_ready": 120, "messages_unacknowledged": 32, "consumers": 8}
        return httpx.Response(200, json=body)

    depth = await _metrics(httpx.MockTransport(handler)).depth("support.emails")

    assert depth == QueueDepth(ready=120, in_flight=32, consumers=8)
    assert depth.total == 152
    assert seen == ["/api/queues/%2F/support.emails"]


async def test_missing_queue_is_empty_and_errors_are_typed() -> None:
    missing = _metrics(httpx.MockTransport(lambda r: httpx.Response(404)))
    broken = _metrics(httpx.MockTransport(lambda r: httpx.Response(500)))

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert await missing.depth("q") == QueueDepth(0)
    with pytest.raises(QueueUnavailableError):
        await broken.depth("q")
    with pytest.raises(QueueUnavailableError):
        await _metrics(httpx.MockTransport(refuse)).depth("q")


def test_compose_commands() -> None:
    scaler = DockerComposeScaler(profiles=("scale",))

    assert scaler.scale_command("queue-worker", 3) == [
        "docker",
        "compose",
        "--profile",
        "scale",
        "up",
        "-d",
        "--no-recreate",
        "--scale",
        "queue-worker=3",
        "queue-worker",
    ]
    assert "--status" not in scaler.ps_command("queue-worker")


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("", 0),
        ('[{"Name":"a"},{"Name":"b"}]', 2),
        ('{"Name":"a"}\n{"Name":"b"}\n{"Name":"c"}\n', 3),
    ],
    ids=["none", "json-array", "json-lines"],
)
async def test_compose_replica_count_parses_both_formats(output: str, expected: int) -> None:
    scaler = DockerComposeScaler()

    async def fake_run(command: list[str]) -> str:
        return output

    scaler._run = fake_run  # type: ignore[method-assign]

    assert await scaler.replicas("queue-worker") == expected

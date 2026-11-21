"""Backlog de Pub/Sub desde Cloud Monitoring con PromQL (10.G1), sin red.

Las respuestas siguen el formato de la API HTTP de Prometheus, que es lo que devuelve el
endpoint de Managed Prometheus; se reemplazan por respuestas capturadas en la primera validación
en la nube (pendientes-cloud.md, fase 10).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from genaipocs_core.adapters.queue_metrics import (
    PUBSUB_BACKLOG_METRIC,
    PubSubBacklogMetrics,
    pubsub_backlog_promql,
)
from genaipocs_core.application.autoscaler import QueueAutoscaler, ScalingTarget
from genaipocs_core.config import GcpSettings, PubSubSettings
from genaipocs_core.domain.autoscaling import ScalingPolicy
from genaipocs_core.ports.scaling import QueueDepth, QueueUnavailableError

PROJECT = "genaipocs-test"
NOW = 1_800_000_000.0


def vector(value: str, *, at: float = NOW - 90) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"metric": {}, "value": [at, value]}]},
    }


EMPTY = {"status": "success", "data": {"resultType": "vector", "result": []}}


def make_metrics(
    respond: Callable[[httpx.Request], httpx.Response], **kwargs: Any
) -> tuple[PubSubBacklogMetrics, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond(request)

    async def token() -> str:
        return "tok-123"

    client = httpx.AsyncClient(
        base_url="https://monitoring.googleapis.com", transport=httpx.MockTransport(handler)
    )
    metrics = PubSubBacklogMetrics(client, token, project=PROJECT, clock=lambda: NOW, **kwargs)
    return metrics, seen


def test_promql_targets_the_subscription_of_the_workers() -> None:
    query = pubsub_backlog_promql(PROJECT, "support.emails.worker")

    assert query == (
        f'sum({PUBSUB_BACKLOG_METRIC}{{monitored_resource="pubsub_subscription",'
        f'project_id="{PROJECT}",subscription_id="support.emails.worker"}})'
    )


async def test_reads_the_backlog_of_the_topic_subscription() -> None:
    metrics, seen = make_metrics(lambda _: httpx.Response(200, json=vector("1234")))

    depth = await metrics.depth("support.emails")

    assert depth == QueueDepth(1234)
    request = seen[0]
    assert request.url.path == f"/v1/projects/{PROJECT}/location/global/prometheus/api/v1/query"
    assert request.url.params["query"] == pubsub_backlog_promql(PROJECT, "support.emails.worker")
    assert request.headers["Authorization"] == "Bearer tok-123"


async def test_no_samples_is_unavailable_so_the_controller_does_not_scale() -> None:
    """Los huecos de métricas no se leen como "cola vacía": bajar a 0 a ciegas sería peor."""
    metrics, _ = make_metrics(lambda _: httpx.Response(200, json=EMPTY))

    with pytest.raises(QueueUnavailableError, match="Sin muestras"):
        await metrics.depth("support.emails")


async def test_value_if_null_like_keda() -> None:
    metrics, _ = make_metrics(lambda _: httpx.Response(200, json=EMPTY), value_if_null=0)

    assert await metrics.depth("support.emails") == QueueDepth(0)


async def test_a_stale_sample_counts_as_missing() -> None:
    old = vector("50", at=NOW - 600)
    metrics, _ = make_metrics(lambda _: httpx.Response(200, json=old), max_sample_age_s=300)

    with pytest.raises(QueueUnavailableError):
        await metrics.depth("support.emails")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}}),
        httpx.Response(200, json={"status": "error", "errorType": "bad_data", "error": "parse"}),
    ],
)
async def test_api_errors_are_unavailable(response: httpx.Response) -> None:
    metrics, _ = make_metrics(lambda _: response)

    with pytest.raises(QueueUnavailableError):
        await metrics.depth("support.emails")


async def test_network_and_token_failures_are_unavailable() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red", request=request)

    metrics, _ = make_metrics(down)
    with pytest.raises(QueueUnavailableError):
        await metrics.depth("support.emails")

    async def no_token() -> str:
        raise RuntimeError("ADC sin credenciales")

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    without_token = PubSubBacklogMetrics(client, no_token, project=PROJECT)
    with pytest.raises(QueueUnavailableError, match="sin token"):
        await without_token.depth("support.emails")


async def test_the_controller_scales_on_the_pubsub_backlog() -> None:
    metrics, _ = make_metrics(lambda _: httpx.Response(200, json=vector("100")))

    class Replicas:
        async def replicas(self, service: str) -> int:
            return 0

        async def scale(self, service: str, replicas: int) -> None:
            raise AssertionError("dry-run no mueve réplicas")

    policy = ScalingPolicy(target_per_replica=20, max_replicas=10)
    controller = QueueAutoscaler(
        metrics, Replicas(), [ScalingTarget("support.emails", "triage", policy)], dry_run=True
    )

    [event] = await controller.tick()

    assert (event.decision.messages, event.decision.desired) == (100, 4)  # +4 por paso.


def test_the_emulator_has_no_backlog_metric() -> None:
    settings = PubSubSettings(emulator_host="127.0.0.1:8085")

    with pytest.raises(ValueError, match="Cloud Monitoring"):
        PubSubBacklogMetrics.from_settings(settings, GcpSettings(), project=PROJECT)


def test_cloud_without_project_fails_loudly() -> None:
    with pytest.raises(ValueError, match="GCP__PROJECT"):
        PubSubBacklogMetrics.from_settings(PubSubSettings(), GcpSettings(), project="")

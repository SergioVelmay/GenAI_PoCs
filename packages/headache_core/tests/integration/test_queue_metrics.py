"""Profundidad de cola contra RabbitMQ real (fase 10.6): lo que lee el autoescalador.

docker compose up -d ; uv run pytest -m integration -k queue_metrics
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

import pytest

from headache_core.adapters.queue_metrics import RabbitMQManagementMetrics
from headache_core.adapters.rabbitmq import RabbitMQMessageBus
from headache_core.config import get_settings
from headache_core.ports import Message
from headache_core.ports.scaling import QueueDepth

pytestmark = pytest.mark.integration


async def test_depth_follows_publishing_and_consuming() -> None:
    settings = get_settings().rabbitmq
    queue = f"it.{uuid.uuid4().hex[:8]}.jobs"
    metrics = RabbitMQManagementMetrics.from_settings(settings)
    bus = await RabbitMQMessageBus.connect(settings, prefetch=2)
    try:
        assert (await metrics.depth(queue)).total == 0  # Aún no existe: vacía.
        for i in range(5):
            await bus.publish(queue, {"n": i})
        # Las estadísticas de la API de gestión se refrescan cada ~5 s.
        depth = await _wait(metrics, queue, lambda d: d.ready == 5)
        assert (depth.total, depth.consumers) == (5, 0)

        release = asyncio.Event()

        async def slow(message: Message) -> None:
            await release.wait()

        await bus.subscribe(queue, slow)
        busy = await _wait(metrics, queue, lambda d: d.in_flight == 2)
        # Lo que cuenta para escalar no cambia al empezar a consumir: 3 esperan y 2 en proceso.
        assert (busy.ready, busy.in_flight, busy.total, busy.consumers) == (3, 2, 5, 1)
        release.set()
    finally:
        await bus.close()
        await metrics.aclose()


async def _wait(
    metrics: RabbitMQManagementMetrics, queue: str, condition: Callable[[QueueDepth], bool]
) -> QueueDepth:
    depth = QueueDepth(0)
    for _ in range(40):
        depth = await metrics.depth(queue)
        if condition(depth):
            return depth
        await asyncio.sleep(0.5)
    raise AssertionError(f"La cola {queue} no llegó al estado esperado: {depth}")

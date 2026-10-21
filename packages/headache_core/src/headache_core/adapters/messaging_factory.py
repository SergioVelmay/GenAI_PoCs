"""Elige el broker: el mismo productor y los mismos workers funcionan sobre los tres.

Lo comparten el indexer (fase 3) y el flujo asíncrono de casos (fase 9.6).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from headache_core.application.telemetry.messaging import TracedMessageBus
from headache_core.config import Settings
from headache_core.ports import MessageBusPort

type Broker = Literal["rabbitmq", "sqs", "servicebus"]
# Valores bien conocidos de `messaging.system` en la convención de OTel.
MESSAGING_SYSTEMS: dict[Broker, str] = {
    "rabbitmq": "rabbitmq",
    "sqs": "aws_sqs",
    "servicebus": "servicebus",
}


@asynccontextmanager
async def open_message_bus(
    settings: Settings,
    broker: Broker,
    *,
    prefetch: int = 8,
    max_deliveries: int = 5,
    retry_delay_ms: int = 1_000,
) -> AsyncIterator[MessageBusPort]:
    """`max_deliveries` y el retardo solo aplican a RabbitMQ: en SQS y Service Bus los fija la
    infraestructura (redrive policy / MaxDeliveryCount), no la aplicación."""
    bus: MessageBusPort
    match broker:
        case "rabbitmq":
            from headache_core.adapters.rabbitmq import RabbitMQMessageBus

            bus = await RabbitMQMessageBus.connect(
                settings.rabbitmq,
                prefetch=prefetch,
                max_deliveries=max_deliveries,
                retry_delay_ms=retry_delay_ms,
            )
        case "sqs":
            from headache_core.adapters.sqs import SqsMessageBus

            bus = SqsMessageBus.from_settings(
                settings.sqs, batch_size=prefetch, retry_delay_s=max(1, retry_delay_ms // 1000)
            )
        case "servicebus":
            from headache_core.adapters.servicebus import ServiceBusMessageBus

            bus = ServiceBusMessageBus.from_settings(settings.service_bus, prefetch=prefetch)
    # Fase 11.3: la traza viaja con el mensaje. Sin telemetría configurada no hace nada.
    traced = TracedMessageBus(bus, system=MESSAGING_SYSTEMS[broker])
    try:
        yield traced
    finally:
        await traced.close()

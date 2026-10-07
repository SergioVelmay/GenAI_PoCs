"""Elige el broker: el mismo productor y el mismo worker funcionan sobre los tres."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from headache_core.config import Settings
from headache_core.ports import MessageBusPort
from indexer.settings import IndexerSettings


@asynccontextmanager
async def open_bus(settings: Settings, config: IndexerSettings) -> AsyncIterator[MessageBusPort]:
    bus: MessageBusPort
    match config.broker:
        case "rabbitmq":
            from headache_core.adapters.rabbitmq import RabbitMQMessageBus

            bus = await RabbitMQMessageBus.connect(
                settings.rabbitmq,
                prefetch=config.prefetch,
                max_deliveries=config.max_deliveries,
                retry_delay_ms=config.retry_delay_ms,
            )
        case "sqs":
            from headache_core.adapters.sqs import SqsMessageBus

            bus = SqsMessageBus.from_settings(
                settings.sqs,
                batch_size=config.prefetch,
                retry_delay_s=max(1, config.retry_delay_ms // 1000),
            )
        case "servicebus":
            from headache_core.adapters.servicebus import ServiceBusMessageBus

            bus = ServiceBusMessageBus.from_settings(settings.service_bus, prefetch=config.prefetch)
    try:
        yield bus
    finally:
        await bus.close()

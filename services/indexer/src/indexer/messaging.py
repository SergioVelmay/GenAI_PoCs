"""Elige el broker del indexer: el mismo productor y el mismo worker funcionan sobre los tres."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from headache_core.adapters.messaging_factory import open_message_bus
from headache_core.config import Settings
from headache_core.ports import MessageBusPort
from indexer.settings import IndexerSettings


@asynccontextmanager
async def open_bus(settings: Settings, config: IndexerSettings) -> AsyncIterator[MessageBusPort]:
    async with open_message_bus(
        settings,
        config.broker,
        prefetch=config.prefetch,
        max_deliveries=config.max_deliveries,
        retry_delay_ms=config.retry_delay_ms,
    ) as bus:
        yield bus

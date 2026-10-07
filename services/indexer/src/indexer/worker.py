"""Worker: consume la cola e indexa. Escala en horizontal lanzando más procesos (competing
consumers); dentro de cada proceso, `INDEXER__PREFETCH` mensajes se procesan a la vez.

Uso:
    uv run python -m indexer.worker
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from headache_core.adapters.rabbitmq import RabbitMQMessageBus
from headache_core.config import get_settings
from indexer.bootstrap import open_pipeline
from indexer.handler import IndexingHandler
from indexer.settings import IndexerSettings

logger = logging.getLogger("indexer.worker")


async def run() -> None:
    settings = get_settings()
    config = IndexerSettings()
    async with open_pipeline(settings, config) as pipeline:
        bus = await RabbitMQMessageBus.connect(
            settings.rabbitmq,
            prefetch=config.prefetch,
            max_deliveries=config.max_deliveries,
            retry_delay_ms=config.retry_delay_ms,
        )
        try:
            await bus.subscribe(config.topic, IndexingHandler(pipeline.indexer))
            logger.info(
                "Escuchando '%s' (prefetch=%d). Ctrl+C para parar.", config.topic, config.prefetch
            )
            await asyncio.Event().wait()
        finally:
            await bus.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s pid=%(process)d %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("aio_pika").setLevel(logging.WARNING)
    logging.getLogger("aiormq").setLevel(logging.WARNING)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()

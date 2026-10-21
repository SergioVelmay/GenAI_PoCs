"""Worker sintético para la demo de autoescalado (fase 10.6). Sin LLM ni coste.

Simula un worker de LLM: tarda `--service-ms` por mensaje (la llamada al modelo) y atiende
`--prefetch` a la vez. Así se ve el autoescalado por cola sin pagar tokens.

    uv run python -m orchestrator.load_worker                       # consumir demo.jobs
    uv run python -m orchestrator.load_worker --publish 300         # ráfaga de 300 trabajos
    docker compose --profile scale up -d queue-worker               # como contenedor
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import random

from headache_core.adapters.messaging_factory import open_message_bus
from headache_core.config import get_settings
from headache_core.ports import Message

logger = logging.getLogger("orchestrator.load_worker")
QUEUE = "demo.jobs"


async def consume(queue: str, *, service_ms: int, prefetch: int) -> None:
    done = 0

    async def handle(message: Message) -> None:
        nonlocal done
        # Latencia de LLM con dispersión: ±30 % alrededor de la media.
        await asyncio.sleep(service_ms / 1000 * random.uniform(0.7, 1.3))
        done += 1
        if done % 20 == 0:
            logger.info("pid=%d: %d trabajos hechos", os.getpid(), done)

    async with open_message_bus(get_settings(), "rabbitmq", prefetch=prefetch) as bus:
        await bus.subscribe(queue, handle)
        logger.info("Consumiendo %s (%d ms por trabajo, %d a la vez)", queue, service_ms, prefetch)
        await asyncio.Event().wait()


async def publish(queue: str, count: int) -> None:
    async with open_message_bus(get_settings(), "rabbitmq") as bus:
        for i in range(count):
            await bus.publish(queue, {"job": i})
    logger.info("Publicados %d trabajos en %s", count, queue)


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker sintético de la demo de autoescalado")
    parser.add_argument("--queue", default=QUEUE)
    parser.add_argument("--service-ms", type=int, default=1000)
    parser.add_argument("--prefetch", type=int, default=4)
    parser.add_argument("--publish", type=int, help="Publica N trabajos y termina")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("aio_pika", "aiormq"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    with contextlib.suppress(KeyboardInterrupt):
        if args.publish:
            asyncio.run(publish(args.queue, args.publish))
        else:
            asyncio.run(consume(args.queue, service_ms=args.service_ms, prefetch=args.prefetch))


if __name__ == "__main__":
    main()

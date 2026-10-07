"""Productor: publica un mensaje por documento del corpus (o borrados, o un mensaje venenoso).

Uso:
    uv run python -m indexer.producer                         # todo el corpus
    uv run python -m indexer.producer --limit 10
    uv run python -m indexer.producer --delete faq-madrid-es
    uv run python -m indexer.producer --poison                # demo de la DLQ
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterator
from pathlib import Path

from headache_core.adapters.rabbitmq import RabbitMQMessageBus
from headache_core.config import get_settings
from indexer.messages import DocumentPayload, IndexingCommand
from indexer.settings import IndexerSettings

DEFAULT_SOURCE = Path("data/synthetic/knowledge/documents.jsonl")


def read_corpus(path: Path) -> Iterator[DocumentPayload]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield DocumentPayload.model_validate_json(line)


def build_commands(args: argparse.Namespace) -> list[IndexingCommand]:
    if args.delete:
        return [IndexingCommand.delete(doc_id) for doc_id in args.delete]
    documents = list(read_corpus(args.source))
    return [IndexingCommand.upsert(doc) for doc in documents[: args.limit]]


async def publish(args: argparse.Namespace) -> None:
    config = IndexerSettings()
    bus = await RabbitMQMessageBus.connect(
        get_settings().rabbitmq,
        max_deliveries=config.max_deliveries,
        retry_delay_ms=config.retry_delay_ms,
    )
    try:
        if args.poison:
            # JSON válido pero contrato inválido (upsert sin documento): error permanente.
            await bus.publish(config.topic, {"action": "upsert", "document_id": "poison"})
            print(f"Mensaje venenoso publicado en '{config.topic}'.")
            return
        commands = build_commands(args)
        for command in commands:
            await bus.publish(config.topic, command.model_dump(mode="json"))
        print(f"{len(commands)} mensajes publicados en '{config.topic}'.")
    finally:
        await bus.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delete", nargs="+", metavar="DOCUMENT_ID")
    parser.add_argument("--poison", action="store_true")
    asyncio.run(publish(parser.parse_args()))


if __name__ == "__main__":
    main()

"""Consulta rápida del índice para verificar la ingesta (el retrieval de verdad es la fase 4).

Uso:
    uv run python -m indexer.search "candlelit refund" --filter language=en --top-k 3
"""

from __future__ import annotations

import argparse
import asyncio

from headache_core.config import get_settings
from headache_core.ports import MetadataValue
from indexer.bootstrap import open_pipeline
from indexer.settings import IndexerSettings


async def search(query: str, filters: dict[str, MetadataValue], top_k: int) -> None:
    async with open_pipeline(get_settings(), IndexerSettings()) as pipeline:
        results = await pipeline.searcher.search(query, top_k=top_k, filters=filters or None)
    for result in results:
        snippet = result.record.text.replace("\n", " ")[:90]
        print(f"{result.score:.3f}  {result.record.id:<28} {snippet}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--filter", action="append", default=[], metavar="CLAVE=VALOR")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    filters: dict[str, MetadataValue] = {}
    for item in args.filter:
        key, _, value = item.partition("=")
        filters[key] = value
    asyncio.run(search(args.query, filters, args.top_k))


if __name__ == "__main__":
    main()

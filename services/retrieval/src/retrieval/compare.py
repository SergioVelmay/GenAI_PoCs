"""Comparacion local de carriles B/C, sin modelos ni escrituras."""

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from headache_core.adapters.memory import HashingEmbedding
from headache_core.application.retrieval import RetrievalRequest
from headache_core.ports.retrieval import RetrievalStrategy
from headache_core.ports.vector_store import MetadataValue
from retrieval.bootstrap import build_local_service, load_postgres, load_synthetic
from retrieval.settings import RetrievalSettings


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    engine: str
    strategy: RetrievalStrategy
    documents: int
    chunks: int
    elapsed_ms: float
    source_ids: tuple[str, ...]


async def compare(
    settings: RetrievalSettings,
    query: str,
    *,
    filters: dict[str, MetadataValue],
    top_k: int = 5,
) -> list[ComparisonRow]:
    rows: list[ComparisonRow] = []
    embeddings = HashingEmbedding(1024)
    records, parents = await (
        load_synthetic(settings, embeddings)
        if settings.source == "synthetic"
        else load_postgres(settings, embeddings)
    )
    for engine in ("native", "llamaindex"):
        service = build_local_service(
            settings.model_copy(update={"engine": engine}),
            records=records,
            parents=parents,
            embeddings=embeddings,
        )
        strategies: list[RetrievalStrategy] = ["bm25", "vector", "hybrid", "parent-child"]
        if engine == "llamaindex":
            strategies.append("auto-merge")
        for strategy in strategies:
            request = RetrievalRequest(
                query,
                strategy=strategy,
                top_k=top_k,
                candidate_k=max(20, top_k),
                filters=filters,
            )
            started = perf_counter()
            response = await service.retrieve(request)
            elapsed = (perf_counter() - started) * 1000
            for result in response.results:
                source = await service.source(result.record.id)
                if source is None or source.text != result.record.text:
                    raise ValueError("Fuente del resultado no verificable")
            rows.append(
                ComparisonRow(
                    engine,
                    strategy,
                    len(parents),
                    len(records),
                    elapsed,
                    tuple(result.record.id for result in response.results),
                )
            )
    return rows


def main() -> None:
    defaults = RetrievalSettings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--source", choices=["synthetic", "postgres"], default=defaults.source)
    parser.add_argument("--documents", type=Path, default=defaults.documents_path)
    parser.add_argument("--limit", type=int, default=defaults.max_documents)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--filter", action="append", default=[], metavar="CLAVE=VALOR")
    args = parser.parse_args()
    filters: dict[str, MetadataValue] = {}
    for item in args.filter:
        key, separator, value = item.partition("=")
        if not separator or not key or not value:
            parser.error("Cada filtro debe tener la forma CLAVE=VALOR")
        filters[key] = value
    try:
        settings = RetrievalSettings(
            source=args.source,
            documents_path=args.documents,
            max_documents=args.limit,
        )
        RetrievalRequest(
            args.query, top_k=args.top_k, candidate_k=max(20, args.top_k), filters=filters
        )
    except ValueError as exc:
        parser.error(str(exc))
    rows = asyncio.run(compare(settings, args.query, filters=filters, top_k=args.top_k))
    print("| Motor | Estrategia | Docs/chunks | ms | IDs de fuente |")
    print("|---|---|---|---:|---|")
    for row in rows:
        print(
            f"| {row.engine} | {row.strategy} | {row.documents}/{row.chunks} | "
            f"{row.elapsed_ms:.2f} | {', '.join(row.source_ids) or '-'} |"
        )


if __name__ == "__main__":
    main()

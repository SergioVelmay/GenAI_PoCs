"""Compara estrategias de chunking (carril nativo y LlamaIndex) sobre el corpus. Sin red ni coste.

Uso:
    uv run python scripts/chunking_report.py
    uv run python scripts/chunking_report.py --doc-type handbook   # solo documentos largos
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable
from pathlib import Path

from llama_index.core.ingestion import run_transformations
from llama_index.core.node_parser import get_leaf_nodes
from llama_index.core.schema import MetadataMode
from llama_index.core.utils import get_tokenizer

from headache_core.adapters.llamaindex import (
    ChunkStrategy,
    PortEmbedding,
    build_chunking,
    to_llama_document,
)
from headache_core.adapters.memory import HashingEmbedding
from headache_core.application.ingestion import Chunker, FixedSizeChunker, SentenceChunker
from headache_core.domain import DocumentType, KnowledgeDocument

SENTENCE_END = (".", "!", "?", ":", "»", '"', ")")


def load_corpus(path: Path, doc_type: str | None) -> list[KnowledgeDocument]:
    docs = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if doc_type and row["doc_type"] != doc_type:
                continue
            docs.append(
                KnowledgeDocument(
                    id=row["id"],
                    title=row["title"],
                    doc_type=DocumentType(row["doc_type"]),
                    language=row["language"],
                    content=row["content"],
                    metadata=row.get("metadata", {}),
                )
            )
    return docs


def native(chunker: Chunker) -> Callable[[list[KnowledgeDocument]], list[str]]:
    return lambda docs: [chunk for doc in docs for chunk in chunker.split(doc.content)]


def llamaindex(strategy: ChunkStrategy) -> Callable[[list[KnowledgeDocument]], list[str]]:
    embed = PortEmbedding(HashingEmbedding(1024), model_name="hashing-1024")
    parsers = build_chunking(strategy, embed_model=embed, chunk_size=128, chunk_overlap=20)

    def run(docs: list[KnowledgeDocument]) -> list[str]:
        nodes = run_transformations([to_llama_document(d) for d in docs], parsers)
        if strategy == "hierarchical":
            nodes = get_leaf_nodes(list(nodes))
        return [n.get_content(metadata_mode=MetadataMode.NONE) for n in nodes]

    return run


STRATEGIES: dict[str, Callable[[list[KnowledgeDocument]], list[str]]] = {
    "B nativo  fixed (80 pal.)": native(FixedSizeChunker(80, 15)),
    "B nativo  sentence (80 pal.)": native(SentenceChunker(80, 15)),
    "C llama   fixed (128 tok.)": llamaindex("fixed"),
    "C llama   sentence (128 tok.)": llamaindex("sentence"),
    "C llama   semantic": llamaindex("semantic"),
    "C llama   hierarchical (hojas)": llamaindex("hierarchical"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("data/synthetic/knowledge/documents.jsonl")
    )
    parser.add_argument("--doc-type", choices=[t.value for t in DocumentType])
    args = parser.parse_args()

    docs = load_corpus(args.source, args.doc_type)
    if not docs:
        raise SystemExit("No hay documentos con ese filtro (¿falta regenerar el corpus?).")
    tokenize = get_tokenizer()
    print(f"{len(docs)} documentos\n")
    print(f"{'estrategia':<32}{'chunks':>8}{'tok.med':>9}{'p95':>6}{'máx':>6}{'corta':>8}{'ms':>8}")
    for name, split in STRATEGIES.items():
        started = time.perf_counter()
        chunks = split(docs)
        elapsed_ms = (time.perf_counter() - started) * 1000
        sizes = sorted(len(tokenize(c)) for c in chunks)
        p95 = sizes[int(0.95 * (len(sizes) - 1))]
        # Chunks que no terminan en fin de frase: el corte ha partido una frase.
        cut = sum(not c.rstrip().endswith(SENTENCE_END) for c in chunks) / len(chunks)
        print(
            f"{name:<32}{len(chunks):>8}{statistics.median(sizes):>9.0f}{p95:>6}"
            f"{sizes[-1]:>6}{cut:>8.0%}{elapsed_ms:>8.0f}"
        )


if __name__ == "__main__":
    main()
